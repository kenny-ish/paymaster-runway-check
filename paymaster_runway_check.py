#!/usr/bin/env python3
"""Estimate how long each ERC-4337 paymaster can keep sponsoring UserOperations
before its EntryPoint deposit runs dry.

Reads UserOperationEvent / Deposited / Withdrawn logs for a block window, calls
EntryPoint.getDepositInfo for every paymaster seen, and turns the observed spend
rate into a runway (time and operation count). Also flags stake problems.
Only a JSON-RPC endpoint is needed.
"""

import argparse
import json
import sys
import urllib.request

USER_AGENT = "paymaster-runway-check/0.1"

ENTRYPOINT_ALIASES = {
    "v0.6": "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789",
    "v0.7": "0x0000000071727de22e5e9d8baf0edac6f37da032",
    "v0.8": "0x4337084d9e255ff0702461cf8895ce9e3b5ff108",
}
ZERO_ADDRESS = "0x" + "00" * 20

# ---------------------------------------------------------------- keccak-256


def _keccak_constants():
    """Round constants via the LFSR and rotation offsets via (t+1)(t+2)/2."""

    def rc_bit(t):
        if t % 255 == 0:
            return 1
        r = 1
        for _ in range(t % 255):
            r <<= 1
            if r & 0x100:
                r ^= 0x171
        return r & 1

    round_constants = []
    for ir in range(24):
        rc = 0
        for j in range(7):
            rc |= rc_bit(j + 7 * ir) << ((1 << j) - 1)
        round_constants.append(rc)

    rotations = [0] * 25
    x, y = 1, 0
    for t in range(24):
        rotations[x + 5 * y] = ((t + 1) * (t + 2) // 2) % 64
        x, y = y, (2 * x + 3 * y) % 5
    return round_constants, rotations


_RC, _ROT = _keccak_constants()
_MASK = (1 << 64) - 1


def _keccak_f(state):
    for rnd in range(24):
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & _MASK) for x in range(5)]
        a = [state[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                r = _ROT[x + 5 * y]
                v = a[x + 5 * y]
                b[y + 5 * ((2 * x + 3 * y) % 5)] = ((v << r) | (v >> (64 - r))) & _MASK if r else v
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        state[0] ^= _RC[rnd]


def keccak256(data: bytes) -> bytes:
    rate = 136
    state = [0] * 25
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(state)
    return b"".join(state[i].to_bytes(8, "little") for i in range(4))[:32]


# ------------------------------------------------------------- ABI helpers


def hexstr(b: bytes) -> str:
    return "0x" + b.hex()


def unhex(s: str) -> bytes:
    s = s[2:] if s.startswith("0x") else s
    return bytes.fromhex(s)


def topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def words(data: bytes):
    return [int.from_bytes(data[i:i + 32], "big") for i in range(0, len(data), 32)]


TOPIC_USEROP_EVENT = keccak256(b"UserOperationEvent(bytes32,address,address,uint256,bool,uint256,uint256)")
TOPIC_DEPOSITED = keccak256(b"Deposited(address,uint256)")
TOPIC_WITHDRAWN = keccak256(b"Withdrawn(address,address,uint256)")
SEL_GET_DEPOSIT_INFO = keccak256(b"getDepositInfo(address)")[:4]


def encode_get_deposit_info(account: str) -> str:
    return hexstr(SEL_GET_DEPOSIT_INFO + bytes(12) + unhex(account))


def decode_deposit_info(result: str) -> dict:
    """DepositInfo(uint112 deposit, bool staked, uint112 stake, uint32 unstakeDelaySec, uint48 withdrawTime)."""
    w = words(unhex(result))
    if len(w) < 5:
        raise ValueError("getDepositInfo returned %d words, expected 5" % len(w))
    return {"deposit": w[0], "staked": w[1] != 0, "stake": w[2],
            "unstakeDelaySec": w[3], "withdrawTime": w[4]}


def decode_log(lg: dict):
    """Return ('op'|'deposit'|'withdraw', fields) or None for other logs."""
    if not lg.get("topics"):
        return None
    t0 = unhex(lg["topics"][0])
    data = words(unhex(lg["data"]))
    block = int(lg["blockNumber"], 16) if isinstance(lg["blockNumber"], str) else lg["blockNumber"]
    if t0 == TOPIC_USEROP_EVENT:
        return "op", {
            "userOpHash": lg["topics"][1],
            "sender": topic_address(lg["topics"][2]),
            "paymaster": topic_address(lg["topics"][3]),
            "nonce": data[0], "success": data[1] != 0,
            "actualGasCost": data[2], "actualGasUsed": data[3],
            "block": block, "tx": lg.get("transactionHash"),
        }
    if t0 == TOPIC_DEPOSITED:
        return "deposit", {"account": topic_address(lg["topics"][1]), "totalDeposit": data[0], "block": block}
    if t0 == TOPIC_WITHDRAWN:
        return "withdraw", {"account": topic_address(lg["topics"][1]),
                            "withdrawAddress": "0x" + ("%064x" % data[0])[-40:],
                            "amount": data[1], "block": block}
    return None


# ------------------------------------------------------------- aggregation


def block_chunks(from_block: int, to_block: int, size: int):
    out = []
    start = from_block
    while start <= to_block:
        end = min(start + size - 1, to_block)
        out.append((start, end))
        start = end + 1
    return out


def empty_stats():
    return {"ops": 0, "failed": 0, "cost": 0, "max_cost": 0, "deposit_events": 0,
            "withdrawn": 0, "withdraw_events": 0, "first_block": None, "last_block": None,
            "senders": set()}


def aggregate(logs):
    """Group decoded logs per paymaster. Returns (stats, self_funded)."""
    stats = {}
    self_funded = {"ops": 0, "failed": 0, "cost": 0}

    def entry(addr):
        if addr not in stats:
            stats[addr] = empty_stats()
        return stats[addr]

    for lg in logs:
        decoded = decode_log(lg)
        if decoded is None:
            continue
        kind, f = decoded
        if kind == "op":
            if f["paymaster"] == ZERO_ADDRESS:
                self_funded["ops"] += 1
                self_funded["failed"] += 0 if f["success"] else 1
                self_funded["cost"] += f["actualGasCost"]
                continue
            e = entry(f["paymaster"])
            e["ops"] += 1
            e["failed"] += 0 if f["success"] else 1
            e["cost"] += f["actualGasCost"]
            e["max_cost"] = max(e["max_cost"], f["actualGasCost"])
            e["senders"].add(f["sender"])
            e["first_block"] = f["block"] if e["first_block"] is None else min(e["first_block"], f["block"])
            e["last_block"] = f["block"] if e["last_block"] is None else max(e["last_block"], f["block"])
        elif kind == "deposit":
            e = entry(f["account"])
            e["deposit_events"] += 1
        elif kind == "withdraw":
            e = entry(f["account"])
            e["withdraw_events"] += 1
            e["withdrawn"] += f["amount"]
    return stats, self_funded


def runway(deposit: int, cost_in_window: int, window_seconds: int, avg_cost: int):
    """Return (runway_seconds or None, ops_left or None). None means unlimited (no spend)."""
    if cost_in_window <= 0 or window_seconds <= 0:
        return None, None
    per_sec = cost_in_window / window_seconds
    secs = deposit / per_sec
    ops_left = deposit // avg_cost if avg_cost > 0 else None
    return secs, ops_left


def stake_flags(info: dict, now_ts: int, min_unstake_delay: int = 86400):
    flags = []
    if not info["staked"]:
        flags.append("not staked")
    elif info["unstakeDelaySec"] < min_unstake_delay:
        flags.append("unstake delay %ds below %ds" % (info["unstakeDelaySec"], min_unstake_delay))
    if info["withdrawTime"]:
        if info["withdrawTime"] <= now_ts:
            flags.append("stake unlocked and withdrawable")
        else:
            flags.append("unstake pending (withdrawable in %dh)" % ((info["withdrawTime"] - now_ts) // 3600))
    return flags


def fmt_duration(secs):
    if secs is None:
        return "no spend"
    if secs >= 86400 * 365:
        return "%.0fy" % (secs / (86400 * 365))
    if secs >= 86400:
        return "%.1fd" % (secs / 86400)
    if secs >= 3600:
        return "%.1fh" % (secs / 3600)
    return "%.0fm" % (secs / 60)


def fmt_eth(wei) -> str:
    s = ("%.9f" % (wei / 1e18)).rstrip("0")
    return s + "0" if s.endswith(".") else s


def build_rows(stats, infos, window_seconds, now_ts, warn_hours, min_unstake_delay=86400):
    rows = []
    for pm, s in stats.items():
        if s["ops"] == 0 and pm not in infos:
            continue
        info = infos.get(pm)
        avg = s["cost"] // s["ops"] if s["ops"] else 0
        secs, ops_left = (None, None)
        if info is not None:
            secs, ops_left = runway(info["deposit"], s["cost"], window_seconds, avg)
        flags = []
        if info is not None:
            if secs is not None and secs < warn_hours * 3600:
                flags.append("LOW RUNWAY")
            if s["max_cost"] and info["deposit"] < s["max_cost"]:
                flags.append("deposit below largest op cost")
            flags += stake_flags(info, now_ts, min_unstake_delay)
        if s["deposit_events"]:
            flags.append("%d top-up(s) in window" % s["deposit_events"])
        if s["withdraw_events"]:
            flags.append("withdrew %s ETH in window" % fmt_eth(s["withdrawn"]))
        rows.append({
            "paymaster": pm, "ops": s["ops"], "failed": s["failed"], "senders": len(s["senders"]),
            "spent": s["cost"], "avg_cost": avg, "max_cost": s["max_cost"],
            "deposit": info["deposit"] if info else None,
            "stake": info["stake"] if info else None,
            "staked": info["staked"] if info else None,
            "unstakeDelaySec": info["unstakeDelaySec"] if info else None,
            "withdrawTime": info["withdrawTime"] if info else None,
            "deposit_events": s["deposit_events"], "withdraw_events": s["withdraw_events"],
            "withdrawn": s["withdrawn"],
            "runway_seconds": secs, "ops_left": ops_left, "flags": flags,
        })
    rows.sort(key=lambda r: (r["runway_seconds"] is None, r["runway_seconds"] or 0, -r["ops"]))
    return rows


def format_table(rows, self_funded, from_block, to_block, window_seconds, entrypoint):
    lines = ["entrypoint %s  blocks %d-%d  window %s" % (entrypoint, from_block, to_block, fmt_duration(window_seconds)),
             "self-funded ops (no paymaster): %d, failed %d, cost %s ETH" % (
                 self_funded["ops"], self_funded["failed"], fmt_eth(self_funded["cost"])), ""]
    head = "%-42s %6s %6s %12s %12s %12s %10s %8s  %s" % (
        "paymaster", "ops", "failed", "spent ETH", "deposit ETH", "stake ETH", "runway", "ops left", "flags")
    lines.append(head)
    for r in rows:
        lines.append("%-42s %6d %6d %12s %12s %12s %10s %8s  %s" % (
            r["paymaster"], r["ops"], r["failed"], fmt_eth(r["spent"]),
            fmt_eth(r["deposit"]) if r["deposit"] is not None else "-",
            fmt_eth(r["stake"]) if r["stake"] is not None else "-",
            fmt_duration(r["runway_seconds"]) if r["deposit"] is not None else "-",
            str(r["ops_left"]) if r["ops_left"] is not None else "-",
            ", ".join(r["flags"]) or "ok"))
    return "\n".join(lines)


# ------------------------------------------------------------------- RPC


class Rpc:
    def __init__(self, url: str):
        self.url = url
        self._id = 0

    def call(self, method: str, params):
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                out = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError("HTTP %d from RPC for %s" % (exc.code, method))
        if "error" in out:
            raise RuntimeError("RPC error %s: %s" % (method, out["error"]))
        return out["result"]


def fetch_logs(rpc, entrypoint, from_block, to_block, chunk):
    logs = []
    topics = [[hexstr(TOPIC_USEROP_EVENT), hexstr(TOPIC_DEPOSITED), hexstr(TOPIC_WITHDRAWN)]]
    for start, end in block_chunks(from_block, to_block, chunk):
        logs += rpc.call("eth_getLogs", [{"fromBlock": hex(start), "toBlock": hex(end),
                                          "address": entrypoint, "topics": topics}])
    return logs


def fetch_deposit_infos(rpc, entrypoint, addresses, block_tag="latest"):
    infos = {}
    for a in addresses:
        res = rpc.call("eth_call", [{"to": entrypoint, "data": encode_get_deposit_info(a)}, block_tag])
        infos[a] = decode_deposit_info(res)
    return infos


def block_timestamp(rpc, number: int) -> int:
    blk = rpc.call("eth_getBlockByNumber", [hex(number), False])
    return int(blk["timestamp"], 16)


def run(rpc, entrypoint, from_block, to_block, chunk, paymasters, warn_hours, min_ops, min_unstake_delay=86400):
    logs = fetch_logs(rpc, entrypoint, from_block, to_block, chunk)
    stats, self_funded = aggregate(logs)
    if paymasters:
        wanted = {p.lower() for p in paymasters}
        for p in wanted:
            stats.setdefault(p, empty_stats())
        stats = {k: v for k, v in stats.items() if k in wanted}
    else:
        stats = {k: v for k, v in stats.items() if v["ops"] >= min_ops}
    t_from = block_timestamp(rpc, from_block)
    t_to = block_timestamp(rpc, to_block)
    window_seconds = max(t_to - t_from, 1)
    infos = fetch_deposit_infos(rpc, entrypoint, sorted(stats))
    rows = build_rows(stats, infos, window_seconds, t_to, warn_hours, min_unstake_delay)
    return rows, self_funded, window_seconds


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paymaster deposit runway check for an ERC-4337 EntryPoint.")
    ap.add_argument("rpc_url", help="JSON-RPC endpoint")
    ap.add_argument("--entrypoint", default="v0.7", help="EntryPoint address or alias v0.6 / v0.7 / v0.8 (default v0.7)")
    ap.add_argument("--blocks", type=int, default=1000, help="window length in blocks ending at --to-block (default 1000)")
    ap.add_argument("--from-block", type=int, help="explicit window start (overrides --blocks)")
    ap.add_argument("--to-block", type=int, help="window end (default latest)")
    ap.add_argument("--chunk", type=int, default=500, help="blocks per eth_getLogs request (default 500)")
    ap.add_argument("--paymaster", action="append", help="only check these paymasters (repeatable)")
    ap.add_argument("--min-ops", type=int, default=1, help="skip paymasters with fewer sponsored ops in the window")
    ap.add_argument("--warn-hours", type=float, default=24.0, help="flag runway shorter than this (default 24)")
    ap.add_argument("--min-unstake-delay", type=int, default=86400, help="flag unstakeDelaySec below this (default 86400)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)

    entrypoint = ENTRYPOINT_ALIASES.get(args.entrypoint.lower(), args.entrypoint).lower()
    rpc = Rpc(args.rpc_url)
    to_block = args.to_block if args.to_block is not None else int(rpc.call("eth_blockNumber", []), 16)
    from_block = args.from_block if args.from_block is not None else max(to_block - args.blocks + 1, 0)
    if from_block >= to_block:
        print("window needs at least two blocks", file=sys.stderr)
        return 2
    rows, self_funded, window_seconds = run(rpc, entrypoint, from_block, to_block, args.chunk,
                                            args.paymaster, args.warn_hours, args.min_ops, args.min_unstake_delay)
    if args.json:
        print(json.dumps({"entrypoint": entrypoint, "from_block": from_block, "to_block": to_block,
                          "window_seconds": window_seconds, "self_funded": self_funded,
                          "paymasters": rows}, indent=2))
    else:
        print(format_table(rows, self_funded, from_block, to_block, window_seconds, entrypoint))
    return 1 if any("LOW RUNWAY" in r["flags"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())

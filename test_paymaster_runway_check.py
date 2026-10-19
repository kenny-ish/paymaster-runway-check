import unittest

import paymaster_runway_check as m

EP_V07 = "0x0000000071727de22e5e9d8baf0edac6f37da032"
TOPIC_OP = m.hexstr(m.TOPIC_USEROP_EVENT)
TOPIC_DEP = m.hexstr(m.TOPIC_DEPOSITED)
TOPIC_WD = m.hexstr(m.TOPIC_WITHDRAWN)

# Optimism tx 0x6ff6277baa037be832137241a0e4c7bbd7d2829c2ca4a637f4edd1bff24df307, block 158041821
OP_LOG = {
    "address": EP_V07,
    "topics": [TOPIC_OP,
               "0xb0a0ed5b3224a4bfdb30d910185fe0d54470c1a598ad24b166ed8b82a9f25a54",
               "0x000000000000000000000000e1b8ac221441a80d5fedd06c4ddbcd3334b261c7",
               "0x00000000000000000000000000e13d60ac6d8dbc9786f77ef9f6696e8e2a075f"],
    "data": "0x9917440000000000008bdaba73cd9815d79069c247eb4bda000000000000000000000000"
            "00000000000000000000000000000000000000000000000000000001"
            "0000000000000000000000000000000000000000000000000000000033992b54"
            "000000000000000000000000000000000000000000000000000000000009435a",
    "blockNumber": "0x96b86dd",
    "transactionHash": "0x6ff6277baa037be832137241a0e4c7bbd7d2829c2ca4a637f4edd1bff24df307",
}

# Ethereum mainnet, EntryPoint v0.7, block 26165919
DEPOSIT_LOG = {
    "address": EP_V07,
    "topics": [TOPIC_DEP, "0x0000000000000000000000003d7d80686a7a0598b97f4fff8b5d87fdf35ed018"],
    "data": "0x000000000000000000000000000000000000000000000000000048695c2681e0",
    "blockNumber": "0x18f429f",
    "transactionHash": "0x4067d0b2471a006a8d92495028e846e7cfbded5c565d65176d6129232afe6785",
}

# Ethereum mainnet, EntryPoint v0.7, block 26163058
WITHDRAW_LOG = {
    "address": EP_V07,
    "topics": [TOPIC_WD, "0x000000000000000000000000ec6b5d04c195eea62ca2991a7db94aab3502effd"],
    "data": "0x0000000000000000000000001101eec94dd79bee1b5a77b96c15ac24a4691e2e"
            "00000000000000000000000000000000000000000000000000004e8adc45f534",
    "blockNumber": "0x18f3772",
    "transactionHash": "0x3a097aee6a791cd84957aee4bcd3dd9a96aa2d295912f3e187f3e21a523e2025",
}

# eth_call EntryPoint v0.7 getDepositInfo(0x2cc0c7981d846b9f2a16276556f6e8cb52bfb633), mainnet, 2026-10-11
DEPOSIT_INFO_HEX = (
    "0x00000000000000000000000000000000000000000000000003e23a84f9b60682"
    "0000000000000000000000000000000000000000000000000000000000000001"
    "000000000000000000000000000000000000000000000000016345785d8a0000"
    "0000000000000000000000000000000000000000000000000000000000015180"
    "0000000000000000000000000000000000000000000000000000000000000000"
)
PM_A = "0x2cc0c7981d846b9f2a16276556f6e8cb52bfb633"
PM_B = "0x00e13d60ac6d8dbc9786f77ef9f6696e8e2a075f"


def op_log(paymaster, cost, success=True, block=100, sender="0x" + "aa" * 20):
    return {
        "address": EP_V07,
        "topics": [TOPIC_OP, "0x" + "11" * 32, "0x" + "00" * 12 + sender[2:], "0x" + "00" * 12 + paymaster[2:]],
        "data": m.hexstr((1).to_bytes(32, "big") + (1 if success else 0).to_bytes(32, "big")
                         + cost.to_bytes(32, "big") + (50000).to_bytes(32, "big")),
        "blockNumber": hex(block), "transactionHash": "0x" + "22" * 32,
    }


class FakeRpc:
    def __init__(self, logs, infos, timestamps):
        self.logs, self.infos, self.timestamps = logs, infos, timestamps
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_getLogs":
            f = params[0]
            lo, hi = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            return [l for l in self.logs if lo <= int(l["blockNumber"], 16) <= hi]
        if method == "eth_call":
            data = params[0]["data"]
            assert data[:10] == "0x5287ce12"
            return self.infos["0x" + data[-40:]]
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(self.timestamps[int(params[0], 16)])}
        raise AssertionError("unexpected " + method)


class KeccakTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(m.keccak256(b"").hex(),
                         "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")

    def test_constants(self):
        self.assertEqual(m._RC[0], 1)
        self.assertEqual(m._RC[23], 0x8000000080008008)
        self.assertEqual(m._ROT[1], 1)
        self.assertEqual(m._ROT[10], 3)
        self.assertEqual(len(set(m._ROT)), 25)

    def test_selectors(self):
        self.assertEqual(m.SEL_GET_DEPOSIT_INFO.hex(), "5287ce12")
        self.assertEqual(m.TOPIC_USEROP_EVENT.hex(), "49628fd1471006c1482da88028e9ce4dbb080b815c9b0344d39e5a8e6ec1419f")
        self.assertEqual(m.TOPIC_DEPOSITED.hex(), "2da466a7b24304f47e87fa2e1e5a81b9831ce54fec19055ce277ca2f39ba42c4")
        self.assertEqual(m.TOPIC_WITHDRAWN.hex(), "d1c19fbcd4551a5edfb66d43d2e337c04837afda3482b42bdf569a8fccdae5fb")


class DecodeTest(unittest.TestCase):
    def test_deposit_info(self):
        info = m.decode_deposit_info(DEPOSIT_INFO_HEX)
        self.assertEqual(info["deposit"], 279850469649942146)
        self.assertTrue(info["staked"])
        self.assertEqual(info["stake"], 10 ** 17)
        self.assertEqual(info["unstakeDelaySec"], 86400)
        self.assertEqual(info["withdrawTime"], 0)
        with self.assertRaises(ValueError):
            m.decode_deposit_info("0x" + "00" * 64)

    def test_encode_call(self):
        self.assertEqual(m.encode_get_deposit_info(PM_A), "0x5287ce12" + "00" * 12 + PM_A[2:])

    def test_userop_event(self):
        kind, f = m.decode_log(OP_LOG)
        self.assertEqual(kind, "op")
        self.assertEqual(f["sender"], "0xe1b8ac221441a80d5fedd06c4ddbcd3334b261c7")
        self.assertEqual(f["paymaster"], PM_B)
        self.assertTrue(f["success"])
        self.assertEqual(f["actualGasCost"], 0x33992b54)
        self.assertEqual(f["actualGasUsed"], 607066)
        self.assertEqual(f["block"], 158041821)

    def test_deposited_event(self):
        kind, f = m.decode_log(DEPOSIT_LOG)
        self.assertEqual(kind, "deposit")
        self.assertEqual(f["account"], "0x3d7d80686a7a0598b97f4fff8b5d87fdf35ed018")
        self.assertEqual(f["totalDeposit"], 79617354793440)

    def test_withdrawn_event(self):
        kind, f = m.decode_log(WITHDRAW_LOG)
        self.assertEqual(kind, "withdraw")
        self.assertEqual(f["account"], "0xec6b5d04c195eea62ca2991a7db94aab3502effd")
        self.assertEqual(f["withdrawAddress"], "0x1101eec94dd79bee1b5a77b96c15ac24a4691e2e")
        self.assertEqual(f["amount"], 86358308025652)

    def test_other_log_ignored(self):
        self.assertIsNone(m.decode_log({"topics": ["0x" + "ab" * 32], "data": "0x", "blockNumber": "0x1"}))
        self.assertIsNone(m.decode_log({"topics": [], "data": "0x", "blockNumber": "0x1"}))


class AggregateTest(unittest.TestCase):
    def test_grouping(self):
        logs = [op_log(PM_A, 100, block=10), op_log(PM_A, 300, success=False, block=12, sender="0x" + "bb" * 20),
                op_log(PM_B, 50, block=11), op_log(m.ZERO_ADDRESS, 7, block=11),
                dict(DEPOSIT_LOG, topics=[TOPIC_DEP, "0x" + "00" * 12 + PM_A[2:]]),
                dict(WITHDRAW_LOG, topics=[TOPIC_WD, "0x" + "00" * 12 + PM_B[2:]])]
        stats, self_funded = m.aggregate(logs)
        self.assertEqual(self_funded, {"ops": 1, "failed": 0, "cost": 7})
        a = stats[PM_A]
        self.assertEqual((a["ops"], a["failed"], a["cost"], a["max_cost"], len(a["senders"])), (2, 1, 400, 300, 2))
        self.assertEqual((a["first_block"], a["last_block"], a["deposit_events"]), (10, 12, 1))
        b = stats[PM_B]
        self.assertEqual((b["ops"], b["withdraw_events"], b["withdrawn"]), (1, 1, 86358308025652))

    def test_block_chunks(self):
        self.assertEqual(m.block_chunks(1, 10, 4), [(1, 4), (5, 8), (9, 10)])
        self.assertEqual(m.block_chunks(5, 5, 100), [(5, 5)])


class RunwayTest(unittest.TestCase):
    def test_runway_math(self):
        secs, ops_left = m.runway(deposit=1000, cost_in_window=100, window_seconds=50, avg_cost=10)
        self.assertEqual(secs, 500.0)
        self.assertEqual(ops_left, 100)
        self.assertEqual(m.runway(1000, 0, 50, 0), (None, None))

    def test_stake_flags(self):
        base = {"staked": True, "unstakeDelaySec": 86400, "withdrawTime": 0}
        self.assertEqual(m.stake_flags(base, 1000), [])
        self.assertEqual(m.stake_flags(dict(base, staked=False), 1000), ["not staked"])
        self.assertEqual(m.stake_flags(dict(base, unstakeDelaySec=60), 1000), ["unstake delay 60s below 86400s"])
        self.assertEqual(m.stake_flags(dict(base, withdrawTime=1000 + 7200), 1000),
                         ["unstake pending (withdrawable in 2h)"])
        self.assertEqual(m.stake_flags(dict(base, withdrawTime=900), 1000), ["stake unlocked and withdrawable"])

    def test_fmt_duration(self):
        self.assertEqual(m.fmt_duration(None), "no spend")
        self.assertEqual(m.fmt_duration(90), "2m")
        self.assertEqual(m.fmt_duration(7200), "2.0h")
        self.assertEqual(m.fmt_duration(3 * 86400), "3.0d")
        self.assertEqual(m.fmt_duration(800 * 86400), "2y")

    def test_build_rows_flags(self):
        stats = {PM_A: dict(m.empty_stats(), ops=4, failed=1, cost=4000, max_cost=1500, senders={"x"}),
                 PM_B: dict(m.empty_stats(), ops=1, cost=10, max_cost=10, senders={"y"}, deposit_events=2)}
        infos = {PM_A: {"deposit": 1000, "staked": False, "stake": 0, "unstakeDelaySec": 0, "withdrawTime": 0},
                 PM_B: {"deposit": 10 ** 18, "staked": True, "stake": 1, "unstakeDelaySec": 86400, "withdrawTime": 0}}
        rows = m.build_rows(stats, infos, window_seconds=3600, now_ts=0, warn_hours=24)
        self.assertEqual(rows[0]["paymaster"], PM_A)
        self.assertAlmostEqual(rows[0]["runway_seconds"], 900.0)
        self.assertEqual(rows[0]["ops_left"], 1)
        self.assertEqual(rows[0]["flags"], ["LOW RUNWAY", "deposit below largest op cost", "not staked"])
        self.assertEqual(rows[1]["flags"], ["2 top-up(s) in window"])
        self.assertEqual(rows[1]["ops_left"], 10 ** 17)


class EndToEndTest(unittest.TestCase):
    def test_run_with_fixed_rpc(self):
        logs = [op_log(PM_A, 10 ** 15, block=101), op_log(PM_A, 3 * 10 ** 15, block=150),
                op_log(PM_B, 5 * 10 ** 14, block=200), op_log(m.ZERO_ADDRESS, 1, block=120)]
        infos = {PM_A: DEPOSIT_INFO_HEX,
                 PM_B: "0x" + "%064x" % (2 * 10 ** 15) + "%064x" % 0 + "%064x" % 0 + "%064x" % 0 + "%064x" % 0}
        rpc = FakeRpc(logs, infos, {100: 1_000_000, 200: 1_001_200})
        rows, self_funded, window = m.run(rpc, EP_V07, 100, 200, 40, None, 24.0, 1)
        self.assertEqual(window, 1200)
        self.assertEqual(self_funded["ops"], 1)
        self.assertEqual(sum(1 for c in rpc.calls if c[0] == "eth_getLogs"), 3)
        self.assertEqual([r["paymaster"] for r in rows], [PM_B, PM_A])
        b, a = rows
        # PM_B: spends 5e14 wei per 1200 s, deposit 2e15 -> 4800 s runway, 4 ops left
        self.assertAlmostEqual(b["runway_seconds"], 4800.0)
        self.assertEqual(b["ops_left"], 4)
        self.assertEqual(b["flags"], ["LOW RUNWAY", "not staked"])
        # PM_A: 4e15 wei per 1200 s against the real 0.27985 ETH deposit -> ~23.3 h
        self.assertEqual(a["deposit"], 279850469649942146)
        self.assertAlmostEqual(a["runway_seconds"], 279850469649942146 / (4 * 10 ** 15 / 1200), places=3)
        self.assertEqual(a["ops_left"], 139)
        self.assertEqual(a["flags"], ["LOW RUNWAY"])
        text = m.format_table(rows, self_funded, 100, 200, window, EP_V07)
        self.assertIn("LOW RUNWAY, not staked", text)
        self.assertIn("23.3h", text)

    def test_run_with_explicit_paymaster(self):
        logs = [op_log(PM_A, 10 ** 15, block=101)]
        infos = {PM_B: "0x" + "%064x" % 10 ** 18 + "%064x" % 1 + "%064x" % 10 ** 17 + "%064x" % 86400 + "%064x" % 0}
        rpc = FakeRpc(logs, infos, {100: 0, 200: 1200})
        rows, _, _ = m.run(rpc, EP_V07, 100, 200, 500, [PM_B], 24.0, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ops"], 0)
        self.assertIsNone(rows[0]["runway_seconds"])
        self.assertEqual(rows[0]["flags"], [])


if __name__ == "__main__":
    unittest.main()

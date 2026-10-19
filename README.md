# paymaster-runway-check

Finds ERC-4337 paymasters that are about to run out of EntryPoint deposit. It reads the `UserOperationEvent`, `Deposited` and `Withdrawn` logs of an EntryPoint over a block window, calls `getDepositInfo` for every paymaster that sponsored an op, and turns the observed spend rate into a runway: how long and how many more ops the current deposit covers. It also flags paymasters that are not staked, have a short unstake delay, or have an unstake in progress. Only a JSON-RPC endpoint is needed; no explorer or indexer API.

## Example

Ethereum mainnet, EntryPoint v0.7, last 600 blocks:

```
$ python paymaster_runway_check.py https://ethereum-rpc.publicnode.com --blocks 600 --chunk 300
entrypoint 0x0000000071727de22e5e9d8baf0edac6f37da032  blocks 26165497-26166096  window 2.0h
self-funded ops (no paymaster): 305, failed 3, cost 0.002747484 ETH

paymaster                                     ops failed    spent ETH  deposit ETH    stake ETH     runway ops left  flags
0x2cc0c7981d846b9f2a16276556f6e8cb52bfb633    235      0  0.004854417  0.279484585          0.1       4.8d    13529  ok
0x777777777777aec03fd955926dbf81597e66834c    491      2  0.013565964  4.507387376          0.0      27.8d   163138  not staked
0x8b1f6cb5d062aa2ce8d581942bbb960420d875ba      9      0  0.000179813  0.092024283      0.10001      42.8d     4605  ok
0x5d0ae8cbad6a88eda0fbe5e916992f9aed417bbb      3      0  0.000161968  0.404774424          0.0     209.0d     7497  not staked
0xe4edf643884a5e0ddfe9d3bc30be2e760504bbb5      3      0  0.000248468  0.690384543          0.0     232.3d     8335  not staked
0x03df76c8c30a88f424cf3cbbc36a1ca02763103b      1      0  0.000877861  7.648366294          0.1         2y     8712  ok
```

Optimism, EntryPoint v0.7, paymasters with at least two sponsored ops in 3000 blocks:

```
$ python paymaster_runway_check.py https://mainnet.optimism.io --blocks 3000 --chunk 1000 --min-ops 2
entrypoint 0x0000000071727de22e5e9d8baf0edac6f37da032  blocks 158039723-158042722  window 1.7h
self-funded ops (no paymaster): 8, failed 0, cost 0.00000057 ETH

paymaster                                     ops failed    spent ETH  deposit ETH    stake ETH     runway ops left  flags
0x777777777777aec03fd955926dbf81597e66834c      9      0  0.000003735  0.059974486          0.1         3y   144508  ok
0x8b1f6cb5d062aa2ce8d581942bbb960420d875ba      2      0  0.000000351  0.009245893        0.101         5y    52638  ok
0x3f222df6ab18c1e10d0ec136503c3b0dfd929048      4      0  0.000000114  0.016447944         0.01        27y   577135  ok
0xd4b53c2ba502a91159685a7951ca44ad1dfa6fa1     37      0  0.000000049  0.266362263          0.0      1032y 200704362  not staked
0x2cc0c7981d846b9f2a16276556f6e8cb52bfb633      4      0  0.000000115  0.629402797          0.1      1039y 21857911  ok
0x00e13d60ac6d8dbc9786f77ef9f6696e8e2a075f     27      0  0.000000034  0.256721603          0.0      1448y 205504024  not staked
```

`--entrypoint v0.6` and `--entrypoint v0.8` (or any EntryPoint address) work the same way; `--json` prints the raw wei values.

## Usage

```
python paymaster_runway_check.py RPC_URL [--entrypoint v0.6|v0.7|v0.8|ADDRESS]
    [--blocks N] [--from-block N] [--to-block N] [--chunk N]
    [--paymaster ADDR ...] [--min-ops N] [--warn-hours H] [--min-unstake-delay SEC] [--json]
```

- `--entrypoint`: EntryPoint alias or address (default `v0.7`).
- `--blocks`: window length ending at `--to-block` (default 1000 blocks ending at the latest block). `--from-block` sets an explicit start.
- `--chunk`: blocks per `eth_getLogs` request (default 500; lower it if the RPC limits the range).
- `--paymaster`: only check the given paymasters (repeatable). They are reported even with zero ops in the window.
- `--min-ops`: hide paymasters with fewer sponsored ops in the window (default 1).
- `--warn-hours`: flag `LOW RUNWAY` when the estimated runway is shorter than this (default 24).
- `--min-unstake-delay`: flag paymasters whose `unstakeDelaySec` is below this (default 86400).

Exit code is 1 when any paymaster is flagged `LOW RUNWAY`, otherwise 0.

## How it works

1. `eth_getLogs` is called per chunk with the EntryPoint address and an OR filter over the three event topics.
2. `UserOperationEvent` logs are grouped by the indexed `paymaster` topic; `actualGasCost` is summed, failures counted, the largest single op cost kept. Ops with a zero paymaster are reported as self-funded. `Deposited` and `Withdrawn` logs are counted per account so top-ups and withdrawals inside the window show up in the flags.
3. `eth_call` to `getDepositInfo(address)` (selector `0x5287ce12`) at the latest block gives `deposit`, `staked`, `stake`, `unstakeDelaySec` and `withdrawTime`.
4. The window length in seconds comes from the timestamps of the first and last block. Runway = deposit / (spent in window / window seconds); ops left = deposit / average op cost. A deposit smaller than the largest op seen is flagged too, since the EntryPoint rejects an op whose prefund is not covered.

The runway is a linear extrapolation of the chosen window, so pick a window that reflects normal traffic. keccak-256 (for selectors and topics) is implemented in the file with constants generated from the Keccak specification.

## Tests

```
python -m unittest -v
```

Tests run offline against fixed RPC responses taken from Ethereum mainnet and Optimism (a real `getDepositInfo` result, real `UserOperationEvent`, `Deposited` and `Withdrawn` logs) plus a fake RPC that drives the full run.

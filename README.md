# Token Spender Allowances

A program that analyzes ERC-20 token approval events across EVM-compatible networks.
Scans historical blockchain data to find all addresses that have approved a specific spender address.
Identifies wallet addresses with both active allowances (> 0) and token balances (> 0) for a given token-spender pair.

## Features

- Scans blockchain history with chunking
- Deduplicates owner addresses to minimize queries
- Uses Multicall3 when available, gracefully falls back to individual queries
- Reports both current allowances and token balances
- Only processes addresses with active allowances > 0
- Results sorted by balance (desc), then allowance (desc)
- Environment-based config with validation
- Works with any EVM-compatible network (Ethereum, BSC, Polygon, etc.)
- Detailed progress tracking and execution time reporting

## Installation

You need Python to run the program.

1. **Setup**:
```bash
pip install -r requirements.txt
```

2. **Configure environment**:
```bash
cp config.env.example config.env
# Edit config.env with your settings
```

3. **Run**:
```bash
python allowance_tracker.py
```

## Key Parameters

| Parameter | Description                          | Required | Default | Notes                                                 |
|-----------|--------------------------------------|----------|---------|-------------------------------------------------------|
| `RPC_URL` | Blockchain RPC endpoint(s)           | Yes | - | Supports multiple URLs separated by commas for failover |
| `TOKEN_ADDRESS` | ERC-20 token contract                | Yes | - | Must be valid ERC-20 contract address                 |
| `SPENDER_ADDRESS` | Address to analyze approvals for     | Yes | - | The spender address you want to analyze               |
| `MULTICALL_ADDRESS` | Multicall3 contract address          | No | `0x0` | Use `0x0` to disable batch optimization               |
| `RPC_TIMEOUT` | RPC request timeout (seconds)        | No | 60 | Increase for slow connections (example uses 180s)     |
| `MAX_RETRIES` | Number of retry attempts             | No | 3 | Increase for unreliable connections (example uses 5)  |
| `RETRY_DELAY` | Base delay between retries (seconds) | No | 1.0 | Uses exponential backoff (example uses 2.0s)          |
| `RATE_LIMIT_DELAY` | Delay between requests (seconds)     | No | 0.5 | Increase to avoid rate limits (example uses 1.0s)     |
| `FROM_BLOCK` | Starting block number                | No | 0 | Set to specific block to limit scan range             |
| `TO_BLOCK` | Ending block number                  | No | `latest` | Use specific block number or `latest` for current     |
| `BLOCK_CHUNK_SIZE` | Blocks per query chunk               | No | 100,000 | Reduce if experiencing timeouts (example uses 50,000) |
| `BATCH_SIZE` | Addresses per multicall batch        | No | 100 | Reduce for stability                  |
| `OUTPUT_FILE` | Output file path                     | No | `active_allowances.txt` | Path for generated report                          |

## Output Format

The program generates a comprehensive CSV report:

```csv
# Token Allowance Analysis Report
# Generated: 2025-01-15 15:30:45 UTC
# Chain ID: 56
# Token: 0x...
# Spender: 0x...
# Block Range: 37000000 to 57000000
# Total Active Allowances: 3
#
# Format: owner_address,allowance_amount,current_balance
# Sorted by: balance DESC, allowance DESC

0x1234567890123456789012345678901234567890,1000000000000000000000,2500000000000000000000
0xabcdefabcdefabcdefabcdefabcdefabcdefabcd,750000000000000000000,2000000000000000000000
0x9876543210987654321098765432109876543210,1200000000000000000000,1500000000000000000000
```

## Project Structure

```
token-allowance-tracker/
├── allowance_tracker.py      # Main application
├── config.env               # Environment configuration
├── requirements.txt         # Python dependencies
├── .gitignore              # Git ignore patterns
├── abis/                   # Contract ABI files
│   ├── erc20.json         # ERC-20 token ABI
│   └── multicall3.json    # Multicall3 ABI
└── README.md              # Documentation
```

## How It Works

1. **📡 Connection Setup**: Establishes Web3 connection with network detection and PoA middleware
2. **📋 Event Discovery**: Scans blockchain history in optimized chunks to find Approval events
3. **🔍 Address Extraction**: Deduplicates owner addresses from discovered events
4. **💰 Allowance Analysis**: Batch queries current allowances (with Multicall3 when available)
5. **📊 Balance Retrieval**: Queries token balances for addresses with active allowances
6. **📈 Data Processing**: Combines and sorts data by balance, then allowance
7. **📄 Report Generation**: Creates comprehensive CSV report with network metadata

## Performance Optimizations

- **Chunked Queries**: Processes large block ranges in manageable chunks with intelligent sizing
- **Adaptive Batching**: Uses Multicall3 when available, falls back gracefully to individual queries
- **Smart Filtering**: Only queries balances for addresses with active allowances
- **Progress Tracking**: Real-time progress updates with percentage completion
- **Memory Efficiency**: Uses sets for deduplication and efficient data structures
- **Advanced Error Recovery**: Multi-level retry logic with exponential backoff
- **Rate Limit Handling**: Intelligent detection and handling of API rate limits
- **RPC Failover**: Automatic failover between multiple RPC endpoints

## Core Dependencies

```
web3>=6.0.0           # Official Ethereum Python library
eth-abi>=4.0.0        # Ethereum ABI encoding/decoding
python-dotenv>=1.0.0  # Environment variable management
requests              # HTTP client (used by web3)
```

## Security Features

- No hardcoded credentials or addresses
- Mandatory environment configuration prevents unsafe defaults
- Address validation prevents invalid inputs
- `.gitignore` prevents accidental commit of sensitive files

#!/usr/bin/env python3
"""
Token Spender Allowances

Queries historical Approval events for a specified spender, deduplicates owners,
batch queries current allowances and balances using multicall, and outputs comprehensive data.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Set

import requests
from dotenv import load_dotenv
from eth_abi import encode
from web3 import Web3
from web3.providers import HTTPProvider

# Import PoA middleware with fallback for different web3 versions
try:
    from web3.middleware import geth_poa_middleware
except ImportError:
    try:
        from web3.middleware.geth_poa import geth_poa_middleware
    except ImportError:
        from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware


class Config:
    """Configuration manager with environment variable support"""
    
    def __init__(self, env_file: str = "config.env"):
        load_dotenv(env_file)
        
        # RPC Configuration
        rpc_url_env = os.getenv("RPC_URL")
        if not rpc_url_env:
            raise ValueError("RPC_URL environment variable is required")
        
        # Parse comma-separated RPC URLs
        self.rpc_urls = [url.strip() for url in rpc_url_env.split(',') if url.strip()]
        if not self.rpc_urls:
            raise ValueError("At least one valid RPC URL is required")
        
        self.rpc_timeout = int(os.getenv("RPC_TIMEOUT") or "60")
        self.max_retries = int(os.getenv("MAX_RETRIES") or "3")
        self.retry_delay = float(os.getenv("RETRY_DELAY") or "1.0")
        self.rate_limit_delay = float(os.getenv("RATE_LIMIT_DELAY") or "0.5")
        
        # Contract Addresses
        self.token_address = os.getenv("TOKEN_ADDRESS")
        if not self.token_address:
            raise ValueError("TOKEN_ADDRESS environment variable is required")
            
        self.spender_address = os.getenv("SPENDER_ADDRESS")
        if not self.spender_address:
            raise ValueError("SPENDER_ADDRESS environment variable is required")
            
        self.multicall_address = os.getenv("MULTICALL_ADDRESS", "0x0000000000000000000000000000000000000000")
        
        # Block Range
        self.from_block = int(os.getenv("FROM_BLOCK") or "0")
        to_block_env = os.getenv("TO_BLOCK") or "latest"
        self.to_block = to_block_env if to_block_env == "latest" else int(to_block_env)
        
        # Performance Settings
        self.batch_size = int(os.getenv("BATCH_SIZE") or "100")
        self.block_chunk_size = int(os.getenv("BLOCK_CHUNK_SIZE") or "100000")
        
        # Output
        self.output_file = os.getenv("OUTPUT_FILE") or "active_allowances.txt"
        
        # Validate configuration
        self._validate_config()
    
    def _validate_config(self) -> None:
        """Validate configuration values"""
        # Validate required addresses
        required_addresses = {
            "TOKEN_ADDRESS": self.token_address,
            "SPENDER_ADDRESS": self.spender_address
        }
        
        for name, address in required_addresses.items():
            if not Web3.is_address(address):
                raise ValueError(f"Invalid {name}: {address}")
        
        # Validate multicall address if provided
        if self.multicall_address != "0x0000000000000000000000000000000000000000":
            if not Web3.is_address(self.multicall_address):
                raise ValueError(f"Invalid MULTICALL_ADDRESS: {self.multicall_address}")
        
        # Validate numeric values
        if self.batch_size <= 0:
            raise ValueError("BATCH_SIZE must be positive")
        if self.block_chunk_size <= 0:
            raise ValueError("BLOCK_CHUNK_SIZE must be positive")
        if self.from_block < 0:
            raise ValueError("FROM_BLOCK must be non-negative")


class ABILoader:
    """Utility class for loading contract ABIs from JSON files"""
    
    @staticmethod
    def load_abi(filename: str) -> List[Dict]:
        """Load ABI from JSON file in abis directory"""
        abi_path = Path("abis") / filename
        try:
            with open(abi_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"ABI file not found: {abi_path}")
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON in ABI file: {abi_path}")


class AllowanceTracker:
    """Main class for tracking token allowances"""
    
    def __init__(self, config: Config):
        self.config = config
        self.owners: Set[str] = set()
        self.use_multicall = False
        
        self._init_web3()
        self._init_contracts()
        self._print_config()
    
    def _init_web3(self) -> None:
        """Initialize Web3 connection with failover support"""
        self.w3 = None
        self.current_rpc_url = None
        
        for i, rpc_url in enumerate(self.config.rpc_urls):
            print(f"Attempting to connect to RPC {i+1}/{len(self.config.rpc_urls)}: {rpc_url}")
            
            try:
                session = requests.Session()
                session.timeout = self.config.rpc_timeout
                
                w3_candidate = Web3(HTTPProvider(rpc_url, session=session))
                
                # Add PoA middleware for networks that require it
                w3_candidate.middleware_onion.inject(geth_poa_middleware, layer=0)
                
                # Test the connection
                if w3_candidate.is_connected():
                    # Test a simple call to make sure it's working
                    chain_id = w3_candidate.eth.chain_id
                    print(f"✓ Successfully connected to {rpc_url}")
                    print(f"✓ Network Chain ID: {chain_id}")
                    
                    self.w3 = w3_candidate
                    self.current_rpc_url = rpc_url
                    self.chain_id = chain_id
                    return
                else:
                    print(f"✗ Connection failed: Not connected")
                    
            except Exception as e:
                print(f"✗ Connection failed: {e}")
                
            # Add delay before trying next RPC
            if i < len(self.config.rpc_urls) - 1:
                print(f"Waiting {self.config.retry_delay}s before trying next RPC...")
                time.sleep(self.config.retry_delay)
        
        # If we get here, all RPCs failed
        rpc_list = ', '.join(self.config.rpc_urls)
        raise ConnectionError(f"Failed to connect to any RPC endpoint: {rpc_list}")
    
    def _init_contracts(self) -> None:
        """Initialize smart contracts"""
        erc20_abi = ABILoader.load_abi("erc20.json")
        
        self.token_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.config.token_address),
            abi=erc20_abi
        )
        
        # Initialize multicall if address is provided
        if self.config.multicall_address != "0x0000000000000000000000000000000000000000":
            try:
                multicall_abi = ABILoader.load_abi("multicall3.json")
                self.multicall_contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(self.config.multicall_address),
                    abi=multicall_abi
                )
                self.use_multicall = True
                print("Multicall3 contract initialized - batch queries enabled")
            except Exception as e:
                print(f"Warning: Could not initialize multicall contract: {e}")
                print("Falling back to individual queries")
                self.use_multicall = False
        else:
            print("No multicall address provided - using individual queries")
            self.use_multicall = False
    
    def _print_config(self) -> None:
        """Print configuration information"""
        print("\nToken Allowance Tracker")
        print("=" * 50)
        print(f"Chain ID: {self.chain_id}")
        print(f"Active RPC: {self.current_rpc_url}")
        print(f"Token: {self.config.token_address}")
        print(f"Spender: {self.config.spender_address}")
        print(f"Output: {self.config.output_file}")
        print(f"Multicall: {'Enabled' if self.use_multicall else 'Disabled'}")
        print("=" * 50)
    
    def _get_approval_events_chunked(self, from_block: int, to_block: int) -> List[Dict]:
        """Get all Approval events using chunked queries"""
        all_events = []
        current_block = from_block
        chunk_size = self.config.block_chunk_size
        
        total_blocks = to_block - from_block + 1
        print(f"Scanning {total_blocks:,} blocks in chunks of {chunk_size:,}")
        
        while current_block <= to_block:
            chunk_end = min(current_block + chunk_size - 1, to_block)
            progress = ((current_block - from_block) / total_blocks) * 100
            
            print(f"Progress: {progress:.1f}% - Blocks {current_block:,} to {chunk_end:,}")
            
            try:
                chunk_events = self._query_chunk_events(current_block, chunk_end)
                all_events.extend(chunk_events)
                
                if chunk_events:
                    print(f"  Found {len(chunk_events):,} events (Total: {len(all_events):,})")
                
            except Exception as e:
                print(f"  Error: {e}")
                if not self._retry_chunk_with_smaller_size(current_block, chunk_end, all_events):
                    print(f"  Skipping problematic chunk")
            
            current_block = chunk_end + 1
            time.sleep(0.1)
        
        print(f"Completed! Total events found: {len(all_events):,}")
        return all_events
    
    def _query_chunk_events(self, from_block: int, to_block: int) -> List[Dict]:
        """Query events for a block range"""
        return self.token_contract.events.Approval.get_logs(
            from_block=from_block,
            to_block=to_block,
            argument_filters={
                'spender': Web3.to_checksum_address(self.config.spender_address)
            }
        )
    
    def _retry_chunk_with_smaller_size(self, start_block: int, end_block: int, all_events: List[Dict]) -> bool:
        """Retry failed chunk with smaller sub-chunks"""
        smaller_chunk = self.config.block_chunk_size // 10
        if smaller_chunk < 1000:
            return False
        
        print(f"  Retrying with smaller chunks of {smaller_chunk:,} blocks")
        
        for sub_start in range(start_block, end_block + 1, smaller_chunk):
            sub_end = min(sub_start + smaller_chunk - 1, end_block)
            try:
                sub_events = self._query_chunk_events(sub_start, sub_end)
                all_events.extend(sub_events)
                if sub_events:
                    print(f"    Sub-chunk {sub_start:,}-{sub_end:,}: {len(sub_events):,} events")
            except Exception as e2:
                print(f"    Failed sub-chunk {sub_start:,}-{sub_end:,}: {e2}")
        
        return True
    
    def _process_approval_events(self, events: List[Dict]) -> None:
        """Extract unique owner addresses from events"""
        initial_count = len(self.owners)
        
        for event in events:
            self.owners.add(event['args']['owner'])
        
        new_owners = len(self.owners) - initial_count
        if new_owners > 0:
            print(f"Found {new_owners:,} new unique owners (Total: {len(self.owners):,})")
    
    def _prepare_multicall_data(self, owners: List[str], query_type: str) -> List[Dict]:
        """Prepare multicall data for batch queries"""
        if query_type == "allowance":
            selector = self.w3.keccak(text="allowance(address,address)")[:4]
            return [
                {
                    'target': self.config.token_address,
                    'callData': selector + encode(
                        ['address', 'address'],
                        [Web3.to_checksum_address(owner), Web3.to_checksum_address(self.config.spender_address)]
                    )
                }
                for owner in owners
            ]
        elif query_type == "balance":
            selector = self.w3.keccak(text="balanceOf(address)")[:4]
            return [
                {
                    'target': self.config.token_address,
                    'callData': selector + encode(['address'], [Web3.to_checksum_address(owner)])
                }
                for owner in owners
            ]
        else:
            raise ValueError(f"Unknown query type: {query_type}")
    
    def _batch_query(self, owners: List[str], query_type: str) -> Dict[str, int]:
        """Query allowances or balances for multiple owners"""
        if not owners:
            return {}
        
        results = {}
        total_batches = (len(owners) + self.config.batch_size - 1) // self.config.batch_size
        
        for i in range(0, len(owners), self.config.batch_size):
            batch = owners[i:i + self.config.batch_size]
            batch_num = i // self.config.batch_size + 1
            
            print(f"  Batch {batch_num}/{total_batches}: {len(batch)} addresses")
            
            if self.use_multicall:
                try:
                    calls = self._prepare_multicall_data(batch, query_type)
                    _, return_data = self.multicall_contract.functions.aggregate(calls).call()
                    
                    for j, owner in enumerate(batch):
                        if j < len(return_data):
                            value = int.from_bytes(return_data[j], byteorder='big')
                            results[owner] = value
                            
                except Exception as e:
                    print(f"    Multicall failed: {e}")
                    results.update(self._individual_queries_fallback(batch, query_type))
            else:
                results.update(self._individual_queries_fallback(batch, query_type))
        
        return results
    
    def _individual_queries_fallback(self, owners: List[str], query_type: str) -> Dict[str, int]:
        """Individual contract calls when multicall is unavailable"""
        results = {}
        
        for owner in owners:
            try:
                if query_type == "allowance":
                    value = self.token_contract.functions.allowance(
                        Web3.to_checksum_address(owner),
                        Web3.to_checksum_address(self.config.spender_address)
                    ).call()
                elif query_type == "balance":
                    value = self.token_contract.functions.balanceOf(
                        Web3.to_checksum_address(owner)
                    ).call()
                else:
                    value = 0
                
                results[owner] = value
                
            except Exception as e:
                print(f"    Error querying {query_type} for {owner}: {e}")
                results[owner] = 0
        
        return results
    
    def _write_results(self, active_data: Dict[str, Dict[str, int]]) -> None:
        """Write results to output file"""
        output_path = Path(self.config.output_file)
        
        sorted_data = sorted(
            active_data.items(),
            key=lambda x: (x[1]['balance'], x[1]['allowance']),
            reverse=True
        )
        
        with open(output_path, 'w') as f:
            f.write("# Token Allowance Analysis Report\n")
            f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
            f.write(f"# Chain ID: {self.chain_id}\n")
            f.write(f"# Token: {self.config.token_address}\n")
            f.write(f"# Spender: {self.config.spender_address}\n")
            f.write(f"# Block Range: {self.config.from_block} to {self.config.to_block}\n")
            f.write(f"# Total Active Allowances: {len(active_data):,}\n")
            f.write("#\n")
            f.write("# Format: owner_address,allowance_amount,current_balance\n")
            f.write("# Sorted by: balance DESC, allowance DESC\n")
            f.write("\n")
            
            for owner, data in sorted_data:
                f.write(f"{owner},{data['allowance']},{data['balance']}\n")
        
        print(f"\nResults written to: {output_path}")
        print(f"Total addresses with active allowances: {len(active_data):,}")
    
    def run(self) -> None:
        """Execute the allowance analysis"""
        start_time = time.time()
        
        to_block = self.w3.eth.block_number if self.config.to_block == "latest" else self.config.to_block
        
        print(f"Scanning blocks {self.config.from_block:,} to {to_block:,}\n")
        
        try:
            print("Phase 1: Fetching approval events")
            events = self._get_approval_events_chunked(self.config.from_block, to_block)
            
            if not events:
                print("No approval events found for the specified spender")
                return
            
            print(f"\nPhase 2: Processing {len(events):,} events")
            self._process_approval_events(events)
            
            if not self.owners:
                print("No unique owners found")
                return
            
            print(f"\nPhase 3: Querying allowances for {len(self.owners):,} owners")
            owners_list = list(self.owners)
            allowances = self._batch_query(owners_list, "allowance")
            
            active_owners = [owner for owner, allowance in allowances.items() if allowance > 0]
            
            if not active_owners:
                print("No addresses found with active allowances > 0")
                return
            
            print(f"Found {len(active_owners):,} addresses with active allowances")
            
            print(f"\nPhase 4: Querying balances for {len(active_owners):,} addresses")
            balances = self._batch_query(active_owners, "balance")
            
            active_data = {
                owner: {
                    'allowance': allowances.get(owner, 0),
                    'balance': balances.get(owner, 0)
                }
                for owner in active_owners
            }
            
            print(f"\nPhase 5: Writing results")
            self._write_results(active_data)
            
        except Exception as e:
            print(f"Error during execution: {e}")
            raise
        finally:
            execution_time = time.time() - start_time
            self._print_execution_time(execution_time)
    
    def _print_execution_time(self, execution_time: float) -> None:
        """Format and print execution time"""
        if execution_time < 60:
            time_str = f"{execution_time:.2f} seconds"
        elif execution_time < 3600:
            minutes = int(execution_time // 60)
            seconds = execution_time % 60
            time_str = f"{minutes}m {seconds:.1f}s"
        else:
            hours = int(execution_time // 3600)
            minutes = int((execution_time % 3600) // 60)
            seconds = execution_time % 60
            time_str = f"{hours}h {minutes}m {seconds:.1f}s"
        
        print(f"\n{'='*50}")
        print(f"Execution completed")
        print(f"Total execution time: {time_str}")
        print(f"{'='*50}")


def main() -> None:
    """Main entry point"""
    try:
        config = Config()
        tracker = AllowanceTracker(config)
        tracker.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
import aiohttp
import asyncio
import time
from typing import List, Dict, Optional, Tuple
import json
from datetime import datetime
import os

# Constants for pipeline configuration
BATCH_SIZE = 50  # Number of leads to process in each batch
MAX_CONCURRENT = 5  # Maximum concurrent requests within a batch
MAX_RETRIES = 3  # Number of retries for failed requests
RETRY_DELAY = 5  # Seconds to wait before retrying
TIMEOUT = 60  # Timeout for each request in seconds
SAVE_INTERVAL = 100  # Save results after every N leads

class EmailGenerationPipeline:
    def __init__(self, 
                 batch_size: int = BATCH_SIZE,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_retries: int = MAX_RETRIES,
                 retry_delay: int = RETRY_DELAY,
                 timeout: int = TIMEOUT,
                 save_interval: int = SAVE_INTERVAL):
        self.batch_size = batch_size
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.save_interval = save_interval
        self.all_results = []  # Store all results (successful and failed)
        
    def get_timestamp(self) -> str:
        """Get current timestamp for file naming"""
        return datetime.now().strftime("%Y%m%d_%H%M%S")
        
    def save_results(self, results: List[dict], batch_num: int, is_final: bool = False):
        """No-op: Results saving is disabled"""
        pass
        
    def save_payloads(self, payloads: List[dict], batch_num: int):
        """No-op: Payload saving is disabled"""
        pass

    async def send_request_with_retry(self, 
                                    session: aiohttp.ClientSession, 
                                    payload: dict, 
                                    lead_name: str) -> Tuple[Optional[dict], bool, dict]:
        """
        Send a request with retry logic.
        Returns: (result, success_status, detailed_result)
        """
        detailed_result = {
            "lead_id": payload["lead"]["lead_id"],
            "lead_name": lead_name,
            "company": payload["lead"]["company"],
            "status": "failed",
            "attempts": [],
            "final_result": None,
            "error": None,
            "payload": payload  # Include the payload in the detailed result
        }
        
        for attempt in range(self.max_retries + 1):
            attempt_info = {
                "attempt_number": attempt + 1,
                "timestamp": datetime.now().isoformat(),
                "status": "failed",
                "error": None,
                "payload": payload  # Include the payload in each attempt
            }
            
            try:
                async with session.post(
                    "https://leadxmail-c6.onrender.com/generate-single-email",
                    json=payload,
                    timeout=self.timeout
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        print(f"✅ Success for {lead_name} (Attempt {attempt + 1})")
                        attempt_info["status"] = "success"
                        detailed_result["status"] = "success"
                        detailed_result["final_result"] = result
                        detailed_result["attempts"].append(attempt_info)
                        return result, True, detailed_result
                    else:
                        error_msg = f"HTTP {response.status}"
                        print(f"❌ Failed for {lead_name} (Status: {response.status}, Attempt {attempt + 1})")
                        attempt_info["error"] = error_msg
                        
            except asyncio.TimeoutError:
                error_msg = "Timeout"
                print(f"⏰ Timeout for {lead_name} (Attempt {attempt + 1})")
                attempt_info["error"] = error_msg
            except Exception as e:
                error_msg = str(e)
                print(f"❌ Error for {lead_name}: {error_msg} (Attempt {attempt + 1})")
                attempt_info["error"] = error_msg
                
            detailed_result["attempts"].append(attempt_info)
            detailed_result["error"] = error_msg
                
            if attempt < self.max_retries:
                print(f"🔄 Retrying {lead_name} in {self.retry_delay} seconds...")
                await asyncio.sleep(self.retry_delay)
                
        return None, False, detailed_result

    async def process_batch(self, 
                          session: aiohttp.ClientSession, 
                          batch: List[dict],
                          batch_num: int) -> List[dict]:
        """Process a single batch of leads"""
        # Save payloads for this batch
        self.save_payloads(batch, batch_num)
        
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def bounded_send_request(payload: dict) -> Tuple[Optional[dict], bool, dict]:
            async with semaphore:
                return await self.send_request_with_retry(
                    session, 
                    payload, 
                    payload["lead"]["name"]
                )
        
        tasks = [bounded_send_request(payload) for payload in batch]
        results = await asyncio.gather(*tasks)
        
        # Store all results (both successful and failed)
        batch_results = [r[2] for r in results]  # Get detailed results
        self.all_results.extend(batch_results)
        
        # Return only successful results for immediate processing
        successful_results = [r[0] for r in results if r[1]]
        return successful_results

    async def process_all_leads(self, all_payloads: List[dict]) -> None:
        """Process all leads in batches"""
        total_leads = len(all_payloads)
        total_processed = 0
        total_successful = 0
        start_time = time.time()
        
        print(f"\n🚀 Starting pipeline for {total_leads} leads")
        print(f"📊 Configuration:")
        print(f"   Batch size: {self.batch_size}")
        print(f"   Max concurrent: {self.max_concurrent}")
        print(f"   Max retries: {self.max_retries}")
        print(f"   Retry delay: {self.retry_delay}s")
        print(f"   Timeout: {self.timeout}s")
        
        # Save all payloads before processing
        self.save_payloads(all_payloads, 0)  # Save complete payload list with batch number 0
        
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            # Process in batches
            for batch_num, i in enumerate(range(0, total_leads, self.batch_size), 1):
                batch = all_payloads[i:i + self.batch_size]
                print(f"\n📦 Processing batch {batch_num} ({len(batch)} leads)")
                
                batch_start = time.time()
                successful_results = await self.process_batch(session, batch, batch_num)
                batch_time = time.time() - batch_start
                
                # Update statistics
                total_processed += len(batch)
                total_successful += len(successful_results)
                
                # Save results if we've processed enough leads
                if total_processed % self.save_interval == 0:
                    self.save_results(successful_results, batch_num)
                
                # Print batch summary
                print(f"\n📊 Batch {batch_num} Summary:")
                print(f"   Processed: {len(batch)} leads")
                print(f"   Successful: {len(successful_results)}")
                print(f"   Failed: {len(batch) - len(successful_results)}")
                print(f"   Batch time: {batch_time:.2f}s")
                print(f"   Average time per lead: {batch_time/len(batch):.2f}s")
                
                # Print overall progress
                progress = (total_processed / total_leads) * 100
                print(f"\n📈 Overall Progress: {progress:.1f}%")
                print(f"   Total processed: {total_processed}/{total_leads}")
                print(f"   Total successful: {total_successful}")
                print(f"   Success rate: {(total_successful/total_processed)*100:.1f}%")
                
                # Add a small delay between batches to prevent overwhelming the API
                if i + self.batch_size < total_leads:
                    await asyncio.sleep(2)
        
        # Final summary
        total_time = time.time() - start_time
        print("\n🎉 Pipeline Complete!")
        print(f"📊 Final Summary:")
        print(f"   Total leads: {total_leads}")
        print(f"   Total successful: {total_successful}")
        print(f"   Total failed: {total_leads - total_successful}")
        print(f"   Success rate: {(total_successful/total_leads)*100:.1f}%")
        print(f"   Total time: {total_time:.2f}s")
        print(f"   Average time per lead: {total_time/total_leads:.2f}s")
        
        # Results are now only stored in memory
        self.all_results = self.all_results



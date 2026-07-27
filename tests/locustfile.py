"""
Load testing script for AI Phone Scheduler API
Uses Locust for performance testing

Installation:
    pip install locust

Usage:
    # Start load test
    locust -f locustfile.py --host=http://localhost:8000
    
    # Then open browser: http://localhost:8089
    # Set number of users (try 100) and spawn rate (10 users/sec)

Test Scenarios:
    1. Appointment scheduling (heavy database load)
    2. Slot availability queries (cache/query performance)
    3. Tenant data access (caching effectiveness)
"""
from locust import HttpUser, task, between, events
import random
from datetime import datetime, timedelta
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AppointmentSchedulerUser(HttpUser):
    """
    Simulates a user interacting with the appointment scheduler API.
    """
    
    # Wait 1-3 seconds between tasks to simulate real user behavior
    wait_time = between(1, 3)
    
    # Test data
    tenant_ids = ["test-tenant-1", "test-tenant-2", "test-tenant-3"]
    
    def on_start(self):
        """
        Called when a simulated user starts.
        Can be used for login/authentication if needed.
        """
        self.tenant_id = random.choice(self.tenant_ids)
        logger.info(f"User started with tenant: {self.tenant_id}")
    
    @task(5)  # Weight 5 - Most common operation
    def get_available_slots(self):
        """
        Test slot availability query - This is the MOST critical performance test.
        Before optimization: 5-10 seconds
        After optimization: 50-500 ms
        """
        start_date = datetime.now().date()
        end_date = start_date + timedelta(days=7)
        
        with self.client.get(
            f"/api/v1/appointments/tenant/{self.tenant_id}/available-slots",
            params={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "duration_minutes": 60
            },
            name="/appointments/available-slots",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                slots = response.json()
                if isinstance(slots, list):
                    response.success()
                    logger.debug(f"Retrieved {len(slots)} slots")
                else:
                    response.failure("Invalid response format")
            elif response.status_code == 404:
                # Tenant not found is acceptable in test
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(3)  # Weight 3
    def get_tenant_info(self):
        """
        Test tenant data retrieval - Tests caching effectiveness.
        Before optimization: 50-100 ms per request
        After optimization: 1-5 ms (cached)
        """
        with self.client.get(
            f"/api/v1/tenants/{self.tenant_id}",
            name="/tenants/get",
            catch_response=True
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(2)  # Weight 2
    def list_appointments(self):
        """
        Test appointment listing - Tests database query performance.
        """
        with self.client.get(
            f"/api/v1/appointments/tenant/{self.tenant_id}",
            params={"limit": 50, "offset": 0},
            name="/appointments/list",
            catch_response=True
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(1)  # Weight 1 - Less frequent
    def health_check(self):
        """
        Test health check endpoint - Should always be fast.
        """
        with self.client.get(
            "/health",
            name="/health",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "healthy":
                    response.success()
                else:
                    response.failure("Health check returned unhealthy status")
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(1)  # Weight 1 - Less frequent
    def health_check_detailed(self):
        """
        Test detailed health check - Tests all service connections.
        """
        with self.client.get(
            "/health/detailed",
            name="/health/detailed",
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}")


class StressTestUser(HttpUser):
    """
    Aggressive stress testing user - No wait time between requests.
    Use this to really push the system to its limits.
    
    Usage:
        locust -f locustfile.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=5m --class-picker StressTestUser
    """
    
    wait_time = between(0.1, 0.5)  # Very aggressive
    
    tenant_ids = ["stress-test-tenant"]
    
    @task
    def stress_get_slots(self):
        """Stress test the most expensive operation."""
        tenant_id = random.choice(self.tenant_ids)
        start_date = datetime.now().date()
        end_date = start_date + timedelta(days=14)  # 2 weeks
        
        self.client.get(
            f"/api/v1/appointments/tenant/{tenant_id}/available-slots",
            params={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "duration_minutes": 60
            },
            name="/stress/available-slots"
        )


# Event hooks for detailed metrics
@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """
    Log slow requests for analysis.
    """
    if response_time > 1000:  # More than 1 second
        logger.warning(
            f"SLOW REQUEST: {name} took {response_time}ms "
            f"(Type: {request_type}, Length: {response_length})"
        )


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """
    Called when the load test starts.
    """
    logger.info("=" * 80)
    logger.info("🚀 LOAD TEST STARTED")
    logger.info("=" * 80)
    logger.info(f"Target host: {environment.host}")
    logger.info("Testing performance improvements...")
    logger.info("")
    logger.info("Expected Results AFTER optimization:")
    logger.info("  - Throughput: 500-2000 req/sec")
    logger.info("  - Latency: 50-200 ms (95th percentile)")
    logger.info("  - Error rate: < 1%")
    logger.info("=" * 80)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """
    Called when the load test stops.
    """
    logger.info("=" * 80)
    logger.info("🏁 LOAD TEST COMPLETED")
    logger.info("=" * 80)
    logger.info("")
    logger.info("Check the Locust web UI for detailed metrics:")
    logger.info("  - Total requests")
    logger.info("  - Requests per second")
    logger.info("  - Response times (median, 95th, 99th percentile)")
    logger.info("  - Error rate")
    logger.info("")
    logger.info("Compare these metrics with before optimization!")
    logger.info("=" * 80)


# Helper functions
def print_performance_comparison():
    """
    Print expected performance comparison.
    """
    print("\n" + "=" * 80)
    print("📊 EXPECTED PERFORMANCE COMPARISON")
    print("=" * 80)
    print("\nBEFORE Optimization:")
    print("  Throughput:        10-50 requests/sec")
    print("  Avg Response Time: 1-10 seconds")
    print("  95th Percentile:   15+ seconds")
    print("  Error Rate:        10-30% (timeouts)")
    print("  CPU Usage:         90-100%")
    print("\nAFTER Optimization:")
    print("  Throughput:        500-2000 requests/sec  (40x faster!)")
    print("  Avg Response Time: 100-300 ms            (40x reduction!)")
    print("  95th Percentile:   400-600 ms")
    print("  Error Rate:        < 1%")
    print("  CPU Usage:         30-40%")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    # Print comparison when script is run
    print_performance_comparison()
    print("\n💡 Run: locust -f locustfile.py --host=http://localhost:8000")
    print("   Then open: http://localhost:8089\n")



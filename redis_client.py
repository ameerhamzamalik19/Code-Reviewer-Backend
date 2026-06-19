import redis
import json

# Connect to the Redis container running on localhost
r = redis.Redis(host='redis', port=6379, decode_responses=True)    #UNCOMMENT THIS LINE IF USING DOCKER
# r = redis.Redis(host='localhost', port=6379, decode_responses=True)   #UNCOMMENT THIS LINE IF RUNNING LOCALLY

# Optional: test the connection
try:
    r.ping()
    print("✅ Redis connected successfully")
except redis.ConnectionError:
    print("❌ Redis connection failed – check if container is running")
    raise
    # You may want to raise an exception or handle gracefully

def cache_pr_review(pr_url: str, issues: list, ttl_seconds: int = 3600):
    """Store the review result with a 1-hour TTL."""
    r.rpush(f"review:{pr_url}", ttl_seconds, json.dumps(issues))

def get_cached_pr_review(pr_url: str):
    """Return cached issues or None if not found."""
    data = r.lindex(f"review:{pr_url}", -1)
    return json.loads(data) if data else None


#  AUTH HELPER FUNCTIONS
def store_refresh_token(user_name: str, token: str, expires_seconds: int = 604800):
    key = f"refresh_token:{user_name}"
    print(f"Storing refresh token in Redis with key: {key} and expires in: {expires_seconds} seconds")  # Debugging line
    r.setex(key, expires_seconds, token)

def get_refresh_token(user_name: str) -> str:
    print(f"Retrieving refresh token from Redis for user: {user_name}")  # Debugging line
    return r.get(f"refresh_token:{user_name}")

def delete_refresh_token(user_name: str):
    r.delete(f"refresh_token:{user_name}")
import requests

API_BASE = "https://app-setter.digitalmarketingservicesnewyork.com/api/v1"
FRONTEND_ORIGIN = "https://appointment-setter-frontend-gptk5pgkx-umairs-projects-271f6cd8.vercel.app"

ENDPOINTS = [
    "/auth/login",
    "/auth/register",
    "/tenants",
    "/agents",
    "/appointments",
    "/health",
]

def test_preflight(endpoint, method="GET"):
    url = API_BASE + endpoint
    headers = {
        "Origin": FRONTEND_ORIGIN,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "Authorization, Content-Type",
    }
    try:
        r = requests.options(url, headers=headers, timeout=10)
        print(f"\n🧪 OPTIONS {endpoint}")
        print("Status:", r.status_code)
        for h in ["access-control-allow-origin", "access-control-allow-methods", "access-control-allow-headers"]:
            if h in r.headers:
                print(f"  {h}: {r.headers[h]}")
            else:
                print(f"  ❌ Missing header: {h}")
    except Exception as e:
        print(f"  ❌ Error testing {endpoint}: {e}")

def test_main_request(endpoint, method="GET"):
    url = API_BASE + endpoint
    headers = {"Origin": FRONTEND_ORIGIN, "Content-Type": "application/json"}
    try:
        if method == "POST":
            r = requests.post(url, headers=headers, json={})
        else:
            r = requests.get(url, headers=headers)
        print(f"\n🌐 {method} {endpoint}")
        print("Status:", r.status_code)
        print("Access-Control-Allow-Origin:", r.headers.get("access-control-allow-origin", "❌ Missing"))
    except Exception as e:
        print(f"  ❌ Error calling {endpoint}: {e}")

def main():
    print("🚀 CORS Diagnostic for Backend:", API_BASE)
    for ep in ENDPOINTS:
        test_preflight(ep, "GET")
        test_main_request(ep, "GET")

if __name__ == "__main__":
    main()



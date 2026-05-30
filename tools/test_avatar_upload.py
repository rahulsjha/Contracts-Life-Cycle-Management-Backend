import io
import sys
import requests
from PIL import Image

API_BASE = 'http://127.0.0.1:11000'
EMAIL = 'rahuljha93102@gmail.com'
PASSWORD = 'Rahuljha@123'

# Create small test image in memory
img = Image.new('RGB', (64, 64), color=(73, 109, 137))
buf = io.BytesIO()
img.save(buf, format='JPEG')
buf.seek(0)

# Login
login_url = f"{API_BASE}/api/auth/login/"
resp = requests.post(login_url, json={'email': EMAIL, 'password': PASSWORD})
print('Login status:', resp.status_code)
try:
    j = resp.json()
except Exception:
    print('Login response not JSON:', resp.text)
    sys.exit(1)

if resp.status_code != 200:
    print('Login failed:', j)
    sys.exit(1)

access = j.get('access')
if not access:
    print('No access token in response:', j)
    sys.exit(1)

print('Got access token, uploading avatar...')
headers = {'Authorization': f'Bearer {access}'}
files = {'avatar': ('test.jpg', buf, 'image/jpeg')}
data = {'add_to_images': 'true', 'purpose': 'profile_test'}
upload_url = f"{API_BASE}/api/auth/avatar/"
resp2 = requests.post(upload_url, headers=headers, files=files, data=data)
print('Upload status:', resp2.status_code)
try:
    print(resp2.json())
except Exception:
    print(resp2.text)

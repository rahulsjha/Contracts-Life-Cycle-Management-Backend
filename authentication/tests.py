"""Authentication API smoke tests."""

import json

from django.test import Client, TestCase

from authentication.models import User


class AuthenticationAPITest(TestCase):
    def setUp(self):
        self.client = Client()
        self.test_email = 'test@example.com'
        self.test_password = 'testpass123'
        self.user = User.objects.create_user(
            email=self.test_email,
            password=self.test_password,
            first_name='Test',
            last_name='User',
            is_active=True,
        )

    def test_user_login_success(self):
        response = self.client.post(
            '/api/v1/auth/login/',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('access', data)
        self.assertIn('refresh', data)
        self.assertIn('user', data)
        self.assertEqual(data['user']['email'], self.test_email)

    def test_user_login_invalid_credentials(self):
        response = self.client.post(
            '/api/v1/auth/login/',
            data=json.dumps({
                'email': self.test_email,
                'password': 'wrongpassword',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn('error', response.json())

    def test_get_current_user(self):
        login_response = self.client.post(
            '/api/v1/auth/login/',
            data=json.dumps({
                'email': self.test_email,
                'password': self.test_password,
            }),
            content_type='application/json',
        )

        token = login_response.json()['access']
        response = self.client.get(
            '/api/v1/auth/me/',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['email'], self.test_email)
        self.assertEqual(data['first_name'], 'Test')
        self.assertEqual(data['last_name'], 'User')

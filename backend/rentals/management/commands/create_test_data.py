"""
Management command to create test data for the rental platform.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from rentals.models import Rental

User = get_user_model()


class Command(BaseCommand):
    help = "Creates test data with rental properties"

    def handle(self, *args, **kwargs):
        self.stdout.write("Creating test data...")

        # Create test landlord user
        landlord, created = User.objects.get_or_create(
            username="landlord_test",
            defaults={
                "email": "landlord@test.com",
                "first_name": "John",
                "last_name": "Landlord",
                "user_type": "landlord",
                "phone_number": "+1234567890",
                "is_verified": True,
            },
        )
        if created:
            landlord.set_password("testpass123")
            landlord.save()
            self.stdout.write(self.style.SUCCESS(f"Created landlord user: {landlord.username}"))

        # Create test tenant user
        tenant, created = User.objects.get_or_create(
            username="tenant_test",
            defaults={
                "email": "tenant@test.com",
                "first_name": "Jane",
                "last_name": "Student",
                "user_type": "tenant",
                "phone_number": "+1234567891",
                "is_verified": True,
            },
        )
        if created:
            tenant.set_password("testpass123")
            tenant.save()
            self.stdout.write(self.style.SUCCESS(f"Created tenant user: {tenant.username}"))

        # Sample rental data
        today = date.today()
        rentals_data = [
            {
                "title": "Cozy Studio Near Campus",
                "description": "Perfect for students! This cozy studio apartment is just a 5-minute walk from the main campus. Features include a modern kitchen, high-speed internet, and a comfortable study area. Utilities included.",
                "property_type": "studio",
                "price": Decimal("850.00"),
                "security_deposit": Decimal("850.00"),
                "bedrooms": 0,
                "bathrooms": 1,
                "square_footage": 450,
                "address": "123 University Ave",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90210",
                "utilities_included": True,
                "furnishing_status": "furnished",
                "parking_available": False,
                "pets_allowed": False,
                "available_from": today,
                "status": "available",
            },
            {
                "title": "Spacious 2BR Apartment with Parking",
                "description": "Beautiful 2-bedroom apartment in a quiet neighborhood. Features include in-unit laundry, central heating/AC, and assigned parking space. Perfect for roommates or small families. Close to bus routes.",
                "property_type": "apartment",
                "price": Decimal("1450.00"),
                "security_deposit": Decimal("1450.00"),
                "bedrooms": 2,
                "bathrooms": 2,
                "square_footage": 900,
                "address": "456 Oak Street",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90211",
                "utilities_included": False,
                "furnishing_status": "unfurnished",
                "parking_available": True,
                "parking_spots": 1,
                "pets_allowed": True,
                "laundry_available": True,
                "available_from": today,
                "status": "available",
            },
            {
                "title": "Luxury 1BR Downtown Condo",
                "description": "Modern luxury condo in the heart of downtown. Features floor-to-ceiling windows, stainless steel appliances, gym access, and rooftop terrace. Walking distance to restaurants and shops.",
                "property_type": "condo",
                "price": Decimal("1800.00"),
                "security_deposit": Decimal("1800.00"),
                "bedrooms": 1,
                "bathrooms": 1,
                "square_footage": 750,
                "address": "789 Main Street",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90212",
                "utilities_included": True,
                "furnishing_status": "furnished",
                "parking_available": True,
                "parking_spots": 1,
                "pets_allowed": False,
                "gym_access": True,
                "internet_included": True,
                "available_from": today + timedelta(days=14),
                "status": "available",
            },
            {
                "title": "Affordable Room in Shared House",
                "description": "Private bedroom in a shared house with other students. Common areas include living room, kitchen, and backyard. Internet and utilities included. Great community atmosphere!",
                "property_type": "room",
                "price": Decimal("600.00"),
                "security_deposit": Decimal("300.00"),
                "bedrooms": 1,
                "bathrooms": 1,
                "square_footage": 150,
                "address": "321 Elm Street",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90213",
                "utilities_included": True,
                "furnishing_status": "furnished",
                "parking_available": True,
                "pets_allowed": False,
                "internet_included": True,
                "available_from": today,
                "status": "available",
            },
            {
                "title": "Modern 3BR House with Backyard",
                "description": "Charming 3-bedroom house perfect for a group of students. Features include updated kitchen, spacious living room, large backyard, and 2-car garage. Pet-friendly!",
                "property_type": "house",
                "price": Decimal("2400.00"),
                "security_deposit": Decimal("2400.00"),
                "bedrooms": 3,
                "bathrooms": 2,
                "square_footage": 1500,
                "address": "654 Pine Avenue",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90214",
                "utilities_included": False,
                "furnishing_status": "unfurnished",
                "parking_available": True,
                "parking_spots": 2,
                "pets_allowed": True,
                "laundry_available": True,
                "available_from": today + timedelta(days=30),
                "status": "available",
            },
            {
                "title": "Bright Townhouse Near Shopping",
                "description": "Sunny 2-bedroom townhouse with private entrance. Features include updated bathroom, eat-in kitchen, and small patio. Close to grocery stores and public transportation.",
                "property_type": "townhouse",
                "price": Decimal("1650.00"),
                "security_deposit": Decimal("1650.00"),
                "bedrooms": 2,
                "bathrooms": 2,
                "square_footage": 1100,
                "address": "987 Maple Drive",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90215",
                "utilities_included": False,
                "furnishing_status": "semi_furnished",
                "parking_available": True,
                "parking_spots": 1,
                "pets_allowed": False,
                "laundry_available": True,
                "available_from": today + timedelta(days=7),
                "status": "available",
            },
            {
                "title": "Student-Friendly 1BR Apartment",
                "description": "Perfect starter apartment for students. Located on campus shuttle route. Features include updated kitchen, lots of natural light, and on-site laundry facilities. Quiet building with study rooms.",
                "property_type": "apartment",
                "price": Decimal("1100.00"),
                "security_deposit": Decimal("1100.00"),
                "bedrooms": 1,
                "bathrooms": 1,
                "square_footage": 650,
                "address": "147 Campus Boulevard",
                "city": "College Town",
                "state": "CA",
                "zip_code": "90216",
                "utilities_included": True,
                "furnishing_status": "furnished",
                "parking_available": True,
                "parking_spots": 1,
                "pets_allowed": False,
                "internet_included": True,
                "laundry_available": True,
                "available_from": today,
                "status": "available",
            },
        ]

        # Create rentals
        created_count = 0
        for rental_data in rentals_data:
            rental, created = Rental.objects.get_or_create(
                title=rental_data["title"], landlord=landlord, defaults=rental_data
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created rental: {rental.title}"))
            else:
                self.stdout.write(self.style.WARNING(f"Rental already exists: {rental.title}"))

        self.stdout.write(self.style.SUCCESS(f"\nSuccessfully created {created_count} rentals"))
        self.stdout.write(
            self.style.SUCCESS(f"Total rentals in database: {Rental.objects.count()}")
        )
        self.stdout.write(self.style.SUCCESS("\nTest credentials:"))
        self.stdout.write(
            self.style.SUCCESS("  Landlord - username: landlord_test, password: testpass123")
        )
        self.stdout.write(
            self.style.SUCCESS("  Tenant - username: tenant_test, password: testpass123")
        )

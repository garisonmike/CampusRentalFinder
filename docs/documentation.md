Senior-dev, ALX-style guide — Rental Finder MVP (Django version)
# 1) Quick MVP recap

Web app where landlords post rentals, students browse & book, leave ratings, and the school can monitor housing quality via admin dashboard.

# 2) Pre-code artifacts (alignment first)

Actors: Student/Tenant, Landlord, School Admin.

Use cases: register/login, post/edit listings, search listings, book, leave rating, admin stats.

Wireframes: Landing, Login/Signup, Listings browse, Listing detail, Landlord dashboard, Tenant dashboard, Admin stats.

ERD:

User: id, name, email, password_hash, role (tenant|landlord|admin)

Property: id, landlord_id FK, title, price, description, lat/lng, images, status

Booking: id, tenant_id FK, property_id FK, start_date, end_date, status

Rating: id, booking_id FK, tenant_id FK, rating, comment

# 3) Repo + tooling

Directory skeleton:

rental-finder/
├── backend/              # Django + DRF
│   ├── manage.py
│   ├── requirements.txt
│   ├── rental_platform/  # settings, urls, wsgi
│   └── apps/
│       ├── accounts/     # users + roles
│       ├── listings/     # properties, ratings
│       └── bookings/     # bookings flow
│
├── frontend/             # React
│   ├── package.json
│   ├── src/
│   └── public/
│
└── docs/                 # diagrams + pitch
    ├── erd.png
    ├── usecases.png
    └── mvp_plan.md


Tools: GitHub, Postman, Figma, Draw.io, Django + DRF, React + Tailwind.

# 4) Scaffolding commands

Backend:

# create backend project
django-admin startproject rental_platform backend
cd backend
python -m venv venv
source venv/bin/activate
pip install django djangorestframework djangorestframework-simplejwt
pip freeze > requirements.txt

# create apps
python manage.py startapp accounts
python manage.py startapp listings
python manage.py startapp bookings


Frontend:

npx create-react-app frontend
cd frontend
npm install axios react-router-dom

# 5) API contract (starter)

Auth

POST /api/auth/register → {name, email, password, role}

POST /api/auth/login → {email, password} → {token, user}

Properties

GET /api/properties

POST /api/properties (landlord)

GET /api/properties/:id

PUT/DELETE /api/properties/:id (landlord only)

Bookings

POST /api/bookings (tenant)

GET /api/bookings (user’s own)

Ratings

POST /api/ratings → {booking_id, rating, comment}

Admin

GET /api/admin/stats

# 6) Concurrent role tasks
Backend A — Accounts

Models: User (with role).

Endpoints: register, login, me.

JWT auth setup.

Backend B — Listings

Models: Property, Rating.

CRUD endpoints.

Tenant can rate after booking.

Backend C — Bookings + Admin

Models: Booking.

Booking flow with status.

Admin stats (count listings, avg ratings).

Frontend A — UI

React scaffolding.

Screens: Login, Signup, Listings, Listing detail, Add listing, Bookings, Admin dashboard.

Frontend B — API integration

Connect auth → store JWT.

Fetch listings → display.

Add listing → form.

Book → booking form.

Leave rating.

Admin → stats page.

# 7) Integration & QA

Walkthrough: landlord posts, tenant books, leaves rating, admin sees stats.

Fix bugs.

Seed data (2 landlords, 2 tenants, 5 properties).

# 8) Demo script (for school)

Landlord adds a listing.

Tenant books and leaves rating.

Show admin dashboard with stats.

Explain benefits: freshmen guidance, housing quality monitoring.

# 9) Deliverables (before pitch)

Working demo (local).

GitHub repo with README + run instructions.

Docs (ERD, wireframes, API contract).

One-pager pitch doc for school.
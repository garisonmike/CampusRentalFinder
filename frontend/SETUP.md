# CampusRentalFinder - Setup Guide

## Overview

This is the frontend application for CampusRentalFinder, a student rental platform built with React, TypeScript, and TailwindCSS.

## Tech Stack

- **React 18** with TypeScript
- **Vite** for fast development and building
- **TailwindCSS** for styling
- **Zustand** for state management
- **Axios** for API calls
- **React Router** for navigation
- **Shadcn UI** for UI components
- **Sonner** for toast notifications

## Getting Started

### 1. Install Dependencies

```bash
npm install
```

### 2. Configure Backend URL

Create a `.env` file in the root directory:

```env
VITE_API_URL=http://localhost:8000/api
```

Update the URL to match your Django backend URL.

### 3. Start Development Server

```bash
npm run dev
```

The application will be available at `http://localhost:8080`

## Project Structure

```
src/
├── components/          # Reusable components
│   ├── ui/             # Shadcn UI components
│   ├── Navbar.tsx      # Navigation bar
│   ├── Footer.tsx      # Footer component
│   ├── RentalCard.tsx  # Rental listing card
│   └── ProtectedRoute.tsx # Route protection wrapper
├── pages/              # Page components
│   ├── HomePage.tsx    # Landing page
│   ├── LoginPage.tsx   # Login page
│   ├── RegisterPage.tsx # Registration page
│   ├── RentalsPage.tsx # Browse rentals
│   ├── RentalDetailPage.tsx # Rental details
│   ├── DashboardPage.tsx # User dashboard
│   ├── ProfilePage.tsx # User profile
│   ├── FavoritesPage.tsx # Saved rentals
│   ├── CreateRentalPage.tsx # Create rental listing
│   └── AdminPage.tsx   # Admin dashboard
├── services/           # API services
│   └── api.ts         # Axios setup and API calls
├── store/             # State management
│   └── authStore.ts   # Authentication store
├── types/             # TypeScript types
│   └── index.ts       # Type definitions
└── App.tsx            # Main app component
```

## Features

### Authentication
- JWT-based authentication
- Role-based access control (Tenant, Landlord, Admin)
- Automatic token refresh
- Secure logout

### For Tenants
- Browse rental listings
- Search and filter properties
- View detailed rental information
- Save favorites
- Leave reviews
- Contact landlords

### For Landlords
- Create and manage rental listings
- View property statistics
- Respond to inquiries

### For Admins
- View platform statistics
- Monitor user activity
- Manage listings and users

## API Integration

The frontend connects to the following Django backend endpoints:

### Auth
- `POST /register/` - Register new user
- `POST /login/` - User login
- `POST /logout/` - User logout
- `POST /token/refresh/` - Refresh JWT token

### Rentals
- `GET /rentals/` - List all rentals
- `GET /rentals/<id>/` - Get rental details
- `POST /rentals/` - Create rental (landlord only)
- `PUT /rentals/<id>/` - Update rental (landlord only)
- `DELETE /rentals/<id>/` - Delete rental (landlord only)
- `GET /featured/` - Get featured rentals
- `GET /recent/` - Get recent rentals
- `GET /top-rated/` - Get top-rated rentals

### Reviews
- `POST /reviews/` - Add review
- `GET /rental/<id>/statistics/` - Get rental stats

### Profile
- `GET /profile/` - Get user profile
- `PUT /profile/` - Update user profile

### Admin
- `GET /admin/statistics/` - Get platform statistics

## Design System

The application uses a custom design system with:

- **Primary Color**: Vibrant green (hsl(142, 71%, 45%))
- **Secondary Color**: Warm brown (hsl(30, 50%, 40%))
- **Background**: Clean white
- **Custom shadows**: Elevation system
- **Smooth transitions**: Consistent animations
- **Responsive layout**: Mobile-first approach

## Building for Production

```bash
npm run build
```

The production build will be in the `dist/` directory.

## Environment Variables

- `VITE_API_URL` - Backend API base URL

## License

This project is part of the CampusRentalFinder platform.

export interface User {
  id: string;
  email: string;
  username: string;
  role: 'tenant' | 'landlord' | 'admin';
  phone?: string;
  profile_picture?: string;
}

export interface Rental {
  id: string;
  title: string;
  description: string;
  address: string;
  city: string;
  price: number;
  bedrooms: number;
  bathrooms: number;
  area: number;
  images: string[];
  landlord: {
    id: string;
    username: string;
    email: string;
  };
  created_at: string;
  is_available: boolean;
  average_rating?: number;
  review_count?: number;
}

export interface Review {
  id: string;
  rental: string;
  user: {
    id: string;
    username: string;
  };
  rating: number;
  comment: string;
  created_at: string;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterData {
  email: string;
  password: string;
  password_confirm: string;
  first_name?: string;
  last_name?: string;
  phone_number?: string;
  user_type: 'tenant' | 'landlord';
}


export interface AuthTokens {
  access: string;
  refresh: string;
}

export interface Statistics {
  total_users: number;
  total_rentals: number;
  total_reviews: number;
  average_rating: number;
}

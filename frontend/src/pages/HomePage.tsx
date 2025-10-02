import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import RentalCard from '@/components/RentalCard';
import { rentalsApi } from '@/services/api';
import type { Rental } from '@/types';
import { Search, Building2, Shield, Star } from 'lucide-react';
import { toast } from 'sonner';

const HomePage = () => {
  const [featuredRentals, setFeaturedRentals] = useState<Rental[]>([]);
  const [recentRentals, setRecentRentals] = useState<Rental[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadRentals();
  }, []);

  const loadRentals = async () => {
    try {
      setIsLoading(true);
      const [featured, recent] = await Promise.all([
        rentalsApi.getFeatured(),
        rentalsApi.getRecent(),
      ]);
      setFeaturedRentals(featured.slice(0, 3));
      setRecentRentals(recent.slice(0, 6));
    } catch (error) {
      toast.error('Failed to load rentals');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSearch = () => {
    if (searchQuery.trim()) {
      window.location.href = `/rentals?search=${encodeURIComponent(searchQuery)}`;
    } else {
      window.location.href = '/rentals';
    }
  };

  return (
    <div className="min-h-screen">
      {/* Hero Section */}
      <section className="relative overflow-hidden bg-gradient-to-br from-primary to-primary-dark py-20 text-white">
        <div className="container relative z-10">
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="mb-6 text-5xl font-bold leading-tight md:text-6xl">
              Find Your Perfect Student Home
            </h1>
            <p className="mb-8 text-xl text-white/90">
              Discover comfortable and affordable student accommodations near your campus
            </p>
            
            {/* Search Bar */}
            <div className="mx-auto flex max-w-2xl gap-2">
              <Input
                type="text"
                placeholder="Search by city, address, or property name..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                className="h-12 bg-white text-foreground"
              />
              <Button size="lg" onClick={handleSearch} className="bg-secondary hover:bg-secondary/90">
                <Search className="mr-2 h-5 w-5" />
                Search
              </Button>
            </div>
          </div>
        </div>
        
        {/* Decorative elements */}
        <div className="absolute top-0 left-0 h-full w-full opacity-10">
          <div className="absolute top-20 left-20 h-64 w-64 rounded-full bg-white blur-3xl" />
          <div className="absolute bottom-20 right-20 h-96 w-96 rounded-full bg-white blur-3xl" />
        </div>
      </section>

      {/* Features */}
      <section className="py-16 bg-muted/30">
        <div className="container">
          <div className="grid gap-8 md:grid-cols-3">
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                <Building2 className="h-8 w-8 text-primary" />
              </div>
              <h3 className="mb-2 text-xl font-semibold">Wide Selection</h3>
              <p className="text-muted-foreground">
                Browse hundreds of verified student accommodations
              </p>
            </div>
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                <Shield className="h-8 w-8 text-primary" />
              </div>
              <h3 className="mb-2 text-xl font-semibold">Verified Listings</h3>
              <p className="text-muted-foreground">
                All properties are verified by our team for your safety
              </p>
            </div>
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                <Star className="h-8 w-8 text-primary" />
              </div>
              <h3 className="mb-2 text-xl font-semibold">Trusted Reviews</h3>
              <p className="text-muted-foreground">
                Read honest reviews from real students
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Featured Rentals */}
      {featuredRentals.length > 0 && (
        <section className="py-16">
          <div className="container">
            <div className="mb-8 flex items-center justify-between">
              <div>
                <h2 className="text-3xl font-bold">Featured Properties</h2>
                <p className="mt-2 text-muted-foreground">
                  Hand-picked selections for you
                </p>
              </div>
              <Link to="/rentals">
                <Button variant="outline">View All</Button>
              </Link>
            </div>
            
            {isLoading ? (
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-96 animate-pulse rounded-lg bg-muted" />
                ))}
              </div>
            ) : (
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {featuredRentals.map((rental) => (
                  <RentalCard key={rental.id} rental={rental} />
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Recent Rentals */}
      {recentRentals.length > 0 && (
        <section className="py-16 bg-muted/30">
          <div className="container">
            <div className="mb-8">
              <h2 className="text-3xl font-bold">Recently Added</h2>
              <p className="mt-2 text-muted-foreground">
                Check out the latest properties
              </p>
            </div>
            
            {isLoading ? (
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {[1, 2, 3, 4, 5, 6].map((i) => (
                  <div key={i} className="h-96 animate-pulse rounded-lg bg-muted" />
                ))}
              </div>
            ) : (
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {recentRentals.map((rental) => (
                  <RentalCard key={rental.id} rental={rental} />
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {/* CTA Section */}
      <section className="py-16">
        <div className="container">
          <div className="rounded-2xl bg-gradient-to-br from-primary to-primary-dark p-12 text-center text-white">
            <h2 className="mb-4 text-3xl font-bold">Ready to Find Your Home?</h2>
            <p className="mb-8 text-xl text-white/90">
              Join thousands of students who found their perfect accommodation
            </p>
            <div className="flex flex-wrap justify-center gap-4">
              <Link to="/rentals">
                <Button size="lg" className="bg-white text-primary hover:bg-white/90">
                  Browse Rentals
                </Button>
              </Link>
              <Link to="/register">
                <Button size="lg" variant="outline" className="border-white text-white hover:bg-white/10">
                  Sign Up Now
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
};

export default HomePage;

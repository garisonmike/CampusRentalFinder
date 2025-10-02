import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import RentalCard from '@/components/RentalCard';
import { rentalsApi } from '@/services/api';
import type { Rental } from '@/types';
import { Plus, Building2, Heart, User } from 'lucide-react';
import { toast } from 'sonner';

const DashboardPage = () => {
  const { user } = useAuthStore();
  const [myRentals, setMyRentals] = useState<Rental[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (user?.role === 'landlord') {
      loadMyRentals();
    } else {
      setIsLoading(false);
    }
  }, [user]);

  const loadMyRentals = async () => {
    try {
      setIsLoading(true);
      const data = await rentalsApi.getAll();
      // In a real app, filter by landlord ID
      setMyRentals(data);
    } catch (error) {
      toast.error('Failed to load your rentals');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen py-8">
      <div className="container">
        <div className="mb-8">
          <h1 className="mb-2 text-4xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground">
            Welcome back, {user?.username}!
          </p>
        </div>

        {/* Quick Actions */}
        <div className="mb-8 grid gap-6 md:grid-cols-3">
          <Link to="/profile">
            <Card className="transition-shadow hover:shadow-lg">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg">My Profile</CardTitle>
                  <User className="h-5 w-5 text-primary" />
                </div>
                <CardDescription>View and edit your profile</CardDescription>
              </CardHeader>
            </Card>
          </Link>

          {user?.role === 'tenant' && (
            <Link to="/favorites">
              <Card className="transition-shadow hover:shadow-lg">
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">Favorites</CardTitle>
                    <Heart className="h-5 w-5 text-primary" />
                  </div>
                  <CardDescription>Your saved properties</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          )}

          <Link to="/rentals">
            <Card className="transition-shadow hover:shadow-lg">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg">Browse Rentals</CardTitle>
                  <Building2 className="h-5 w-5 text-primary" />
                </div>
                <CardDescription>Find your perfect home</CardDescription>
              </CardHeader>
            </Card>
          </Link>
        </div>

        {/* Landlord Section */}
        {user?.role === 'landlord' && (
          <div>
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h2 className="text-2xl font-bold">My Listings</h2>
                <p className="text-muted-foreground">Manage your rental properties</p>
              </div>
              <Link to="/rentals/create">
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  Add New Rental
                </Button>
              </Link>
            </div>

            {isLoading ? (
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-96 animate-pulse rounded-lg bg-muted" />
                ))}
              </div>
            ) : myRentals.length === 0 ? (
              <Card>
                <CardContent className="flex min-h-[300px] flex-col items-center justify-center">
                  <Building2 className="mb-4 h-16 w-16 text-muted-foreground" />
                  <p className="mb-2 text-xl font-semibold">No listings yet</p>
                  <p className="mb-4 text-muted-foreground">Start by adding your first rental property</p>
                  <Link to="/rentals/create">
                    <Button>
                      <Plus className="mr-2 h-4 w-4" />
                      Add New Rental
                    </Button>
                  </Link>
                </CardContent>
              </Card>
            ) : (
              <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {myRentals.map((rental) => (
                  <RentalCard key={rental.id} rental={rental} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tenant Section */}
        {user?.role === 'tenant' && (
          <Card>
            <CardHeader>
              <CardTitle>Getting Started</CardTitle>
              <CardDescription>Start your search for the perfect accommodation</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex items-start gap-4">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-white">1</div>
                  <div>
                    <h3 className="font-semibold">Browse Rentals</h3>
                    <p className="text-sm text-muted-foreground">Explore our wide selection of student accommodations</p>
                  </div>
                </div>
                <div className="flex items-start gap-4">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-white">2</div>
                  <div>
                    <h3 className="font-semibold">Save Favorites</h3>
                    <p className="text-sm text-muted-foreground">Keep track of properties you're interested in</p>
                  </div>
                </div>
                <div className="flex items-start gap-4">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-white">3</div>
                  <div>
                    <h3 className="font-semibold">Contact Landlords</h3>
                    <p className="text-sm text-muted-foreground">Get in touch with property owners directly</p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

export default DashboardPage;

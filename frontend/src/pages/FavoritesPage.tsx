import { useState } from 'react';
import RentalCard from '@/components/RentalCard';
import { Card, CardContent } from '@/components/ui/card';
import { Heart } from 'lucide-react';

const FavoritesPage = () => {
  // This would normally fetch from an API
  const [favorites] = useState([]);

  return (
    <div className="min-h-screen py-8">
      <div className="container">
        <div className="mb-8">
          <h1 className="mb-2 text-4xl font-bold">My Favorites</h1>
          <p className="text-muted-foreground">Properties you've saved for later</p>
        </div>

        {favorites.length === 0 ? (
          <Card>
            <CardContent className="flex min-h-[400px] flex-col items-center justify-center">
              <Heart className="mb-4 h-16 w-16 text-muted-foreground" />
              <p className="mb-2 text-xl font-semibold">No favorites yet</p>
              <p className="text-muted-foreground">
                Start browsing rentals and save your favorites here
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {favorites.map((rental: any) => (
              <RentalCard key={rental.id} rental={rental} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default FavoritesPage;

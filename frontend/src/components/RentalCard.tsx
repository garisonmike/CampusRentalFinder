import { Link } from 'react-router-dom';
import { Card, CardContent, CardFooter } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { MapPin, Bed, Bath, Maximize, Star } from 'lucide-react';
import type { Rental } from '@/types';

interface RentalCardProps {
  rental: Rental;
}

const RentalCard = ({ rental }: RentalCardProps) => {
  const primaryImage = rental.images?.[0] || '/placeholder.svg';

  return (
    <Link to={`/rentals/${rental.id}`}>
      <Card className="group overflow-hidden transition-all hover:shadow-lg">
        <div className="relative aspect-[4/3] overflow-hidden">
          <img
            src={primaryImage}
            alt={rental.title}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-110"
          />
          {!rental.is_available && (
            <Badge className="absolute top-3 right-3 bg-destructive">
              Unavailable
            </Badge>
          )}
          {rental.average_rating && (
            <div className="absolute top-3 left-3 flex items-center gap-1 rounded-full bg-background/90 px-2 py-1 text-xs font-medium backdrop-blur">
              <Star className="h-3 w-3 fill-primary text-primary" />
              <span>{rental.average_rating.toFixed(1)}</span>
            </div>
          )}
        </div>

        <CardContent className="p-4">
          <h3 className="mb-2 line-clamp-1 font-semibold text-lg">{rental.title}</h3>
          <div className="mb-3 flex items-center gap-1 text-sm text-muted-foreground">
            <MapPin className="h-4 w-4" />
            <span className="line-clamp-1">{rental.address}, {rental.city}</span>
          </div>
          
          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <div className="flex items-center gap-1">
              <Bed className="h-4 w-4" />
              <span>{rental.bedrooms}</span>
            </div>
            <div className="flex items-center gap-1">
              <Bath className="h-4 w-4" />
              <span>{rental.bathrooms}</span>
            </div>
            <div className="flex items-center gap-1">
              <Maximize className="h-4 w-4" />
              <span>{rental.area} m²</span>
            </div>
          </div>
        </CardContent>

        <CardFooter className="border-t p-4">
          <div className="flex w-full items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">Price per month</p>
              <p className="font-bold text-2xl text-primary">${rental.price}</p>
            </div>
            {rental.review_count && rental.review_count > 0 && (
              <p className="text-xs text-muted-foreground">
                {rental.review_count} {rental.review_count === 1 ? 'review' : 'reviews'}
              </p>
            )}
          </div>
        </CardFooter>
      </Card>
    </Link>
  );
};

export default RentalCard;

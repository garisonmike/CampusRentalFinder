import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { rentalsApi, reviewsApi } from '@/services/api';
import { useAuthStore } from '@/store/authStore';
import type { Rental, Review } from '@/types';
import { 
  MapPin, 
  Bed, 
  Bath, 
  Maximize, 
  Star, 
  User,
  Mail,
  Phone,
  ArrowLeft
} from 'lucide-react';
import { toast } from 'sonner';

const RentalDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuthStore();
  const [rental, setRental] = useState<Rental | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (id) {
      loadRental();
    }
  }, [id]);

  const loadRental = async () => {
    try {
      setIsLoading(true);
      const data = await rentalsApi.getById(id!);
      setRental(data);
      // In a real app, load reviews here
      setReviews([]);
    } catch (error) {
      toast.error('Failed to load rental details');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmitReview = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!user) {
      toast.error('Please login to leave a review');
      return;
    }

    try {
      setIsSubmitting(true);
      await reviewsApi.create({
        rental: id!,
        rating,
        comment,
      });
      setComment('');
      setRating(5);
      toast.success('Review submitted successfully!');
      loadRental();
    } catch (error: any) {
      toast.error(error.response?.data?.message || 'Failed to submit review');
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen py-8">
        <div className="container">
          <div className="h-96 animate-pulse rounded-lg bg-muted" />
        </div>
      </div>
    );
  }

  if (!rental) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-xl text-muted-foreground">Rental not found</p>
      </div>
    );
  }

  const primaryImage = rental.images?.[0] || '/placeholder.svg';

  return (
    <div className="min-h-screen py-8">
      <div className="container max-w-5xl">
        <Link to="/rentals">
          <Button variant="ghost" className="mb-4 gap-2">
            <ArrowLeft className="h-4 w-4" />
            Back to Rentals
          </Button>
        </Link>

        {/* Image Gallery */}
        <div className="mb-8 overflow-hidden rounded-2xl">
          <img
            src={primaryImage}
            alt={rental.title}
            className="h-[400px] w-full object-cover"
          />
        </div>

        <div className="grid gap-8 lg:grid-cols-3">
          {/* Main Content */}
          <div className="lg:col-span-2 space-y-6">
            <div>
              <div className="mb-2 flex items-center gap-2">
                <h1 className="text-3xl font-bold">{rental.title}</h1>
                {!rental.is_available && (
                  <Badge variant="destructive">Unavailable</Badge>
                )}
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <MapPin className="h-5 w-5" />
                <span>{rental.address}, {rental.city}</span>
              </div>
            </div>

            {/* Features */}
            <Card>
              <CardContent className="pt-6">
                <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
                  <div className="flex flex-col items-center gap-2">
                    <Bed className="h-8 w-8 text-primary" />
                    <div className="text-center">
                      <p className="font-semibold">{rental.bedrooms}</p>
                      <p className="text-sm text-muted-foreground">Bedrooms</p>
                    </div>
                  </div>
                  <div className="flex flex-col items-center gap-2">
                    <Bath className="h-8 w-8 text-primary" />
                    <div className="text-center">
                      <p className="font-semibold">{rental.bathrooms}</p>
                      <p className="text-sm text-muted-foreground">Bathrooms</p>
                    </div>
                  </div>
                  <div className="flex flex-col items-center gap-2">
                    <Maximize className="h-8 w-8 text-primary" />
                    <div className="text-center">
                      <p className="font-semibold">{rental.area} m²</p>
                      <p className="text-sm text-muted-foreground">Area</p>
                    </div>
                  </div>
                  {rental.average_rating && (
                    <div className="flex flex-col items-center gap-2">
                      <Star className="h-8 w-8 fill-primary text-primary" />
                      <div className="text-center">
                        <p className="font-semibold">{rental.average_rating.toFixed(1)}</p>
                        <p className="text-sm text-muted-foreground">Rating</p>
                      </div>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Description */}
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 text-xl font-semibold">Description</h2>
                <p className="text-muted-foreground leading-relaxed">{rental.description}</p>
              </CardContent>
            </Card>

            {/* Reviews */}
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-4 text-xl font-semibold">Reviews</h2>
                
                {user && user.role === 'tenant' && (
                  <form onSubmit={handleSubmitReview} className="mb-6 space-y-4 rounded-lg border p-4">
                    <div className="space-y-2">
                      <Label>Your Rating</Label>
                      <div className="flex gap-1">
                        {[1, 2, 3, 4, 5].map((star) => (
                          <button
                            key={star}
                            type="button"
                            onClick={() => setRating(star)}
                            className="transition-colors"
                          >
                            <Star
                              className={`h-6 w-6 ${
                                star <= rating
                                  ? 'fill-primary text-primary'
                                  : 'text-muted-foreground'
                              }`}
                            />
                          </button>
                        ))}
                      </div>
                    </div>
                    
                    <div className="space-y-2">
                      <Label htmlFor="comment">Your Review</Label>
                      <Textarea
                        id="comment"
                        placeholder="Share your experience..."
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        rows={3}
                        required
                      />
                    </div>
                    
                    <Button type="submit" disabled={isSubmitting}>
                      {isSubmitting ? 'Submitting...' : 'Submit Review'}
                    </Button>
                  </form>
                )}

                {reviews.length === 0 ? (
                  <p className="text-center text-muted-foreground py-8">
                    No reviews yet. Be the first to review!
                  </p>
                ) : (
                  <div className="space-y-4">
                    {reviews.map((review) => (
                      <div key={review.id} className="rounded-lg border p-4">
                        <div className="mb-2 flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <User className="h-5 w-5 text-muted-foreground" />
                            <span className="font-medium">{review.user.username}</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <Star className="h-4 w-4 fill-primary text-primary" />
                            <span className="font-medium">{review.rating}</span>
                          </div>
                        </div>
                        <p className="text-sm text-muted-foreground">{review.comment}</p>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            <Card>
              <CardContent className="pt-6">
                <div className="mb-6 text-center">
                  <p className="text-sm text-muted-foreground">Price per month</p>
                  <p className="text-4xl font-bold text-primary">${rental.price}</p>
                </div>
                <Button className="w-full" size="lg">
                  Contact Landlord
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-6">
                <h3 className="mb-4 font-semibold">Landlord Information</h3>
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <User className="h-5 w-5 text-muted-foreground" />
                    <span className="text-sm">{rental.landlord.username}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <Mail className="h-5 w-5 text-muted-foreground" />
                    <span className="text-sm">{rental.landlord.email}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RentalDetailPage;

import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { Button } from '@/components/ui/button';
import { Home, Building2, User, LogOut, Heart, LayoutDashboard, Shield } from 'lucide-react';

const Navbar = () => {
  const { isAuthenticated, user, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/');
  };

  return (
    <nav className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-16 items-center justify-between">
        <Link to="/" className="flex items-center gap-2 font-bold text-2xl text-primary">
          <Building2 className="h-7 w-7" />
          <span>CampusRentalFinder</span>
        </Link>

        <div className="flex items-center gap-6">
          <Link to="/" className="flex items-center gap-2 text-sm font-medium transition-colors hover:text-primary">
            <Home className="h-4 w-4" />
            <span className="hidden sm:inline">Home</span>
          </Link>
          <Link to="/rentals" className="flex items-center gap-2 text-sm font-medium transition-colors hover:text-primary">
            <Building2 className="h-4 w-4" />
            <span className="hidden sm:inline">Browse Rentals</span>
          </Link>

          {isAuthenticated ? (
            <>
              <Link to="/dashboard" className="flex items-center gap-2 text-sm font-medium transition-colors hover:text-primary">
                <LayoutDashboard className="h-4 w-4" />
                <span className="hidden sm:inline">Dashboard</span>
              </Link>
              
              {user?.role === 'tenant' && (
                <Link to="/favorites" className="flex items-center gap-2 text-sm font-medium transition-colors hover:text-primary">
                  <Heart className="h-4 w-4" />
                  <span className="hidden sm:inline">Favorites</span>
                </Link>
              )}
              
              {user?.role === 'admin' && (
                <Link to="/admin" className="flex items-center gap-2 text-sm font-medium transition-colors hover:text-primary">
                  <Shield className="h-4 w-4" />
                  <span className="hidden sm:inline">Admin</span>
                </Link>
              )}

              <Link to="/profile">
                <Button variant="ghost" size="sm" className="gap-2">
                  <User className="h-4 w-4" />
                  <span className="hidden sm:inline">{user?.username}</span>
                </Button>
              </Link>
              
              <Button variant="ghost" size="sm" onClick={handleLogout} className="gap-2">
                <LogOut className="h-4 w-4" />
                <span className="hidden sm:inline">Logout</span>
              </Button>
            </>
          ) : (
            <>
              <Link to="/login">
                <Button variant="ghost" size="sm">Login</Button>
              </Link>
              <Link to="/register">
                <Button size="sm">Sign Up</Button>
              </Link>
            </>
          )}
        </div>
      </div>
    </nav>
  );
};

export default Navbar;

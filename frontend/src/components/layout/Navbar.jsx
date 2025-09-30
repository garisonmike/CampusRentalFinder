import { HomeIcon, UserIcon } from '@heroicons/react/24/outline'
import { Link } from 'react-router-dom'
import { useAuthStore } from '../../services/store/authStore'

const Navbar = () => {
    const { user, isAuthenticated } = useAuthStore()

    return (
        <nav className="bg-dark-900 border-b border-dark-800">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <div className="flex justify-between items-center h-16">
                    {/* Logo */}
                    <Link to="/" className="flex items-center space-x-2">
                        <HomeIcon className="h-6 w-6 text-primary-500" />
                        <span className="text-xl font-bold text-white">
                            CampusRentalFinder
                        </span>
                    </Link>

                    {/* Navigation Links */}
                    <div className="hidden md:flex items-center space-x-8">
                        <Link
                            to="/rentals"
                            className="text-gray-300 hover:text-white transition-colors"
                        >
                            Browse Rentals
                        </Link>
                        <Link
                            to="/dashboard"
                            className="text-gray-300 hover:text-white transition-colors"
                        >
                            Dashboard
                        </Link>
                        <Link
                            to="/profile"
                            className="text-gray-300 hover:text-white transition-colors"
                        >
                            Profile
                        </Link>
                    </div>

                    {/* Auth Buttons */}
                    <div className="flex items-center space-x-4">
                        {isAuthenticated ? (
                            <div className="flex items-center space-x-3">
                                <span className="text-gray-300 text-sm hidden md:block">
                                    {user?.first_name}
                                </span>
                                <Link
                                    to="/profile"
                                    className="p-2 text-gray-300 hover:text-white transition-colors"
                                >
                                    <UserIcon className="h-5 w-5" />
                                </Link>
                            </div>
                        ) : (
                            <Link
                                to="/login"
                                className="flex items-center space-x-2 px-4 py-2 rounded-lg border border-primary-600 text-primary-400 hover:bg-primary-900/20 transition-colors"
                            >
                                <UserIcon className="h-4 w-4" />
                                <span>Sign In</span>
                            </Link>
                        )}
                    </div>
                </div>
            </div>
        </nav>
    )
}

export default Navbar
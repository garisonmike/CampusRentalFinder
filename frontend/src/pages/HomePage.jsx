import { HomeIcon as HouseIcon, MagnifyingGlassIcon, ShieldCheckIcon, StarIcon, UserGroupIcon } from '@heroicons/react/24/outline'
import { CheckCircleIcon } from '@heroicons/react/24/solid'
import { Link } from 'react-router-dom'

const HomePage = () => {
    return (
        <div className="min-h-screen bg-dark-900">
            {/* Hero Section */}
            <section className="relative bg-gradient-to-br from-dark-900 via-dark-800 to-dark-900 text-white py-24">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center max-w-4xl mx-auto">
                        <h1 className="text-5xl md:text-6xl font-bold mb-6 leading-tight">
                            Find Your Perfect
                            <span className="block text-transparent bg-clip-text bg-gradient-to-r from-primary-400 to-primary-600">
                                Student Housing
                            </span>
                        </h1>
                        <p className="text-xl text-gray-300 mb-8 max-w-2xl mx-auto">
                            Connect with verified landlords and discover safe, affordable rentals near your campus.
                            Join thousands of students who've found their ideal home.
                        </p>

                        {/* Search Bar */}
                        <div className="flex flex-col sm:flex-row gap-3 justify-center mb-8">
                            <Link
                                to="/rentals"
                                className="inline-flex items-center justify-center space-x-2 px-8 py-4 bg-dark-800 border border-dark-700 text-white rounded-lg hover:bg-dark-700 transition-all"
                            >
                                <MagnifyingGlassIcon className="h-5 w-5" />
                                <span>Browse Rentals</span>
                            </Link>
                            <Link
                                to="/rentals/create"
                                className="inline-flex items-center justify-center space-x-2 px-8 py-4 bg-primary-600 border border-primary-600 text-white rounded-lg hover:bg-primary-700 transition-all"
                            >
                                <HouseIcon className="h-5 w-5" />
                                <span>List a Rental</span>
                            </Link>
                        </div>
                    </div>
                </div>
            </section>

            {/* Why Choose Section */}
            <section className="py-20 bg-dark-900">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-4xl font-bold text-white mb-4">
                            Why Choose CampusRentalFinder?
                        </h2>
                        <p className="text-xl text-gray-400">
                            We prioritize safety, transparency, and student needs
                        </p>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                        {/* Feature 1 - Verified Landlords */}
                        <div className="bg-dark-800 border border-dark-700 rounded-2xl p-8 hover:border-primary-600 transition-all">
                            <div className="flex flex-col items-center text-center">
                                <div className="mb-6">
                                    <ShieldCheckIcon className="h-16 w-16 text-primary-500" />
                                </div>
                                <h3 className="text-2xl font-semibold text-white mb-4">
                                    Verified Landlords
                                </h3>
                                <p className="text-gray-400 mb-6">
                                    All landlords go through our comprehensive verification process
                                </p>
                                <ul className="text-left space-y-3 w-full">
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>Background checks</span>
                                    </li>
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>Property verification</span>
                                    </li>
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>Reviews & ratings</span>
                                    </li>
                                </ul>
                            </div>
                        </div>

                        {/* Feature 2 - Student-Focused */}
                        <div className="bg-dark-800 border border-dark-700 rounded-2xl p-8 hover:border-primary-600 transition-all">
                            <div className="flex flex-col items-center text-center">
                                <div className="mb-6">
                                    <UserGroupIcon className="h-16 w-16 text-primary-500" />
                                </div>
                                <h3 className="text-2xl font-semibold text-white mb-4">
                                    Student-Focused
                                </h3>
                                <p className="text-gray-400 mb-6">
                                    Built specifically for university students and their unique needs
                                </p>
                                <ul className="text-left space-y-3 w-full">
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>Campus proximity</span>
                                    </li>
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>Student budgets</span>
                                    </li>
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>Roommate matching</span>
                                    </li>
                                </ul>
                            </div>
                        </div>

                        {/* Feature 3 - Trusted Platform */}
                        <div className="bg-dark-800 border border-dark-700 rounded-2xl p-8 hover:border-primary-600 transition-all">
                            <div className="flex flex-col items-center text-center">
                                <div className="mb-6">
                                    <StarIcon className="h-16 w-16 text-primary-500" />
                                </div>
                                <h3 className="text-2xl font-semibold text-white mb-4">
                                    Trusted Platform
                                </h3>
                                <p className="text-gray-400 mb-6">
                                    Join thousands of satisfied students and landlords
                                </p>
                                <ul className="text-left space-y-3 w-full">
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>5000+ happy tenants</span>
                                    </li>
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>800+ verified properties</span>
                                    </li>
                                    <li className="flex items-start text-gray-300">
                                        <CheckCircleIcon className="h-5 w-5 text-green-500 mr-2 flex-shrink-0 mt-0.5" />
                                        <span>4.8/5 average rating</span>
                                    </li>
                                </ul>
                            </div>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    )
}

export default HomePage
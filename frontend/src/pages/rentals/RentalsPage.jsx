import { useState } from 'react'
import { Link } from 'react-router-dom'

// Main RentalsPage
const RentalsPage = () => {
    const [searchTerm, setSearchTerm] = useState('')
    const [filters, setFilters] = useState({
        minPrice: '',
        maxPrice: '',
        bedrooms: '',
        propertyType: '',
    })

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-8">
                <h1 className="text-3xl font-bold mb-4">Browse Rental Properties</h1>
                {/* ... your search/filters UI ... */}
            </div>

            {/* Results */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {[1, 2, 3, 4, 5, 6].map((item) => (
                    <div key={item} className="bg-white rounded-lg shadow-md overflow-hidden hover:shadow-lg transition-shadow">
                        <div className="bg-zinc-200 h-48 flex items-center justify-center">
                            <p className="text-gray-500">Property Image</p>
                        </div>
                        <div className="p-4">
                            <h3 className="text-lg font-semibold mb-2">Sample Property {item}</h3>
                            <p className="text-gray-600 text-sm mb-2">123 University Ave, College Town</p>
                            <p className="text-lg font-bold text-blue-600 mb-3">${800 + (item * 50)}/month</p>
                            <div className="flex justify-between items-center text-sm text-gray-600 mb-3">
                                <span>🛏️ 2 bed</span>
                                <span>🚿 1 bath</span>
                                <span>📍 0.5 mi to campus</span>
                            </div>
                            <div className="flex space-x-2">
                                <Link to={`/rentals/${item}`} className="btn btn-primary flex-1 text-center text-sm">
                                    View Details
                                </Link>
                                <button className="btn btn-outline text-sm px-3">❤️</button>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            <div className="text-center mt-8">
                <button className="btn btn-outline">Load More Properties</button>
            </div>
        </div>
    )
}

// Other pages
export const RentalDetailPage = () => (
    <div>
        <h2>Rental Detail Page</h2>
        {/* ... */}
    </div>
)
export const CreateRentalPage = () => (
    <div>
        <h2>Create Rental Page</h2>
        {/* ... */}
    </div>
)
export const DashboardPage = () => (
    <div>
        <h2>Dashboard Page</h2>
        {/* ... */}
    </div>
)
export const ProfilePage = () => (
    <div>
        <h2>Profile Page</h2>
        {/* ... */}
    </div>
)
export const FavoritesPage = () => (
    <div>
        <h2>Favorites Page</h2>
        {/* ... */}
    </div>
)
export const NotFoundPage = () => (
    <div>
        <h2>404 - Not Found</h2>
        {/* ... */}
    </div>
)

export default RentalsPage

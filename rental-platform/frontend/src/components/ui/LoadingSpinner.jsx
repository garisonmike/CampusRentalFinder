const LoadingSpinner = ({ size = 'medium', className = '' }) => {
    const sizeClasses = {
        small: 'h-4 w-4',
        medium: 'h-8 w-8',
        large: 'h-12 w-12',
        extra: 'h-16 w-16'
    }

    return (
        <div className={`inline-block animate-spin rounded-full border-2 border-solid border-current border-r-transparent ${sizeClasses[size]} ${className}`}>
            <span className="sr-only">Loading...</span>
        </div>
    )
}

export default LoadingSpinner
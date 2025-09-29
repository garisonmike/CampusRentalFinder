import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],

    // Path resolution
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
            '@components': path.resolve(__dirname, './src/components'),
            '@pages': path.resolve(__dirname, './src/pages'),
            '@services': path.resolve(__dirname, './src/services'),
            '@utils': path.resolve(__dirname, './src/utils'),
            '@styles': path.resolve(__dirname, './src/styles'),
        },
    },

    // Development server configuration
    server: {
        port: 3000,
        host: true,
        proxy: {
            // Proxy API requests to Django backend
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
                secure: false,
            },
            // Proxy media files to Django backend
            '/media': {
                target: 'http://localhost:8000',
                changeOrigin: true,
                secure: false,
            },
        },
    },

    // Build configuration
    build: {
        outDir: 'dist',
        sourcemap: true,
        rollupOptions: {
            output: {
                manualChunks: {
                    // Vendor chunks for better caching
                    vendor: ['react', 'react-dom'],
                    router: ['react-router-dom'],
                    api: ['axios', 'react-query'],
                },
            },
        },
    },

    // Environment variables
    define: {
        __APP_VERSION__: JSON.stringify(process.env.npm_package_version),
    },

    // CSS configuration
    css: {
        postcss: './postcss.config.js',
    },

    // Test configuration
    test: {
        globals: true,
        environment: 'jsdom',
        setupFiles: ['./src/test/setup.js'],
    },
})
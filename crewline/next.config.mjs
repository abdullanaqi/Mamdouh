/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The crew PWA service worker + manifest live in /public.
  // Headers ensure the service worker can control the crew scope.
  async headers() {
    return [
      {
        source: '/sw.js',
        headers: [
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
          { key: 'Service-Worker-Allowed', value: '/' },
        ],
      },
    ];
  },
};

export default nextConfig;

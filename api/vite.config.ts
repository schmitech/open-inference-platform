import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    lib: {
      entry: 'api.ts',
      formats: ['es', 'cjs'],
      fileName: (format) => `api.${format === 'es' ? 'mjs' : 'cjs'}`
    },
    rollupOptions: {
      external: [
        'react', 
        'react-dom', 
        'dotenv', 
        'url', 
        'path', 
        'fs',
        'node:url',
        'node:path',
        'node:fs'
      ],
      output: {
        globals: {
          react: 'React',
          'react-dom': 'ReactDOM'
        }
      }
    },
    sourcemap: true,
    // Ensure Node.js modules are properly handled
    commonjsOptions: {
      esmExternals: true,
    }
  }
}); 
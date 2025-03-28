import path from 'path';
import { fileURLToPath } from 'node:url';
import fs from 'fs/promises';
import yaml from 'js-yaml';
import { AppConfig } from './types';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Loads and validates the application configuration
 */
export async function loadConfig(): Promise<AppConfig> {
  // Load .env file
  dotenv.config();

  try {
    const configPath = path.resolve(__dirname, '../config.yaml');
    console.log('Loading config from:', configPath);
    
    const configFile = await fs.readFile(configPath, 'utf-8');
    const config = yaml.load(configFile) as AppConfig;
    
    // Update config with environment variables
    if (process.env.ELASTICSEARCH_USERNAME && process.env.ELASTICSEARCH_PASSWORD) {
      config.elasticsearch.auth = {
        username: process.env.ELASTICSEARCH_USERNAME,
        password: process.env.ELASTICSEARCH_PASSWORD
      };
    }
    
    if (process.env.ELEVEN_LABS_API_KEY) {
      config.eleven_labs.api_key = process.env.ELEVEN_LABS_API_KEY;
    }

    // Validate required configuration
    validateConfig(config);
    
    return config;
  } catch (error) {
    console.error('Error reading config file:', error);
    process.exit(1);
  }
}

/**
 * Validates that required configuration values exist
 */
export function validateConfig(config: AppConfig): void {
  // Check if system prompt exists in config
  if (!config.system?.prompt) {
    console.error('No system prompt found in config.yaml. Exiting...');
    process.exit(1);
  }

  // Log system prompt info if verbose
  if (config.general?.verbose === 'true') {
    console.log('Using system prompt from config.yaml:');
    console.log(config.system.prompt.substring(0, 100) + '...');
    console.log('Full system prompt length:', config.system.prompt.length);
  }
}
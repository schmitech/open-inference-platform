"""
Open Inference Server
==================

A modular FastAPI server that provides a chat endpoint with Ollama LLM integration
and Chroma vector database for retrieval augmented generation.

This implementation follows object-oriented principles to create a maintainable
and well-structured application.

Usage:
    python main.py

Features:
    - Chat endpoint with context-aware responses
    - Health check endpoint
    - ChromaDB integration for document retrieval
    - Ollama integration for embeddings and LLM responses
    - Safety check for user queries using GuardrailService
    - HTTPS support using provided certificates
    - API key management
"""

import os
import ssl
import logging
import logging.handlers
import json
import asyncio
from typing import Dict, Any, Optional, List, Callable, Awaitable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import chromadb
from langchain_ollama import OllamaEmbeddings
from dotenv import load_dotenv
from pythonjsonlogger import jsonlogger

# Load environment variables
load_dotenv()

# Import local modules (ensure these exist in your project structure)
from config.config_manager import load_config, _is_true_value
from models.schema import ChatMessage, ApiKeyCreate, ApiKeyResponse, ApiKeyDeactivate
from models import ChatMessage, HealthStatus
from clients.ollama_client import OllamaClient
from clients.chroma_client import ChromaRetriever
from services import ChatService, HealthService, LoggerService, GuardrailService, RerankerService, ApiKeyService


class InferenceServer:
    """
    A modular inference server built with FastAPI that provides chat endpoints
    with LLM integration and vector database retrieval capabilities.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the InferenceServer with optional custom configuration path.
        
        Args:
            config_path: Optional path to a custom configuration file
        """
        # Initialize basic logging until proper configuration is loaded
        self._setup_initial_logging()
        
        # Load configuration
        self.config = load_config(config_path)
        
        # Configure proper logging with loaded configuration
        self._setup_logging()
        
        # Thread pool for blocking I/O operations
        self.thread_pool = ThreadPoolExecutor(max_workers=10)
        
        # Initialize FastAPI app with lifespan manager
        self.app = FastAPI(
            title="Open Inference Server",
            description="A FastAPI server with chat endpoint and RAG capabilities",
            version="1.0.0",
            lifespan=self._create_lifespan_manager()
        )
        
        # Initialize application state
        self.services = {}
        self.clients = {}
        
        # Configure middleware and routes
        self._configure_middleware()
        self._configure_routes()
        
        self.logger.info("InferenceServer initialized")

    def _setup_initial_logging(self) -> None:
        """Set up basic logging configuration before loading the full config."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        )
        self.logger = logging.getLogger(__name__)
        
        # Set specific logger levels for more detailed debugging
        logging.getLogger('clients.ollama_client').setLevel(logging.DEBUG)

    def _setup_logging(self) -> None:
        """Configure logging based on the application configuration."""
        log_config = self.config.get('logging', {})
        log_level = getattr(logging, log_config.get('level', 'INFO').upper())
        
        # Create logs directory if it doesn't exist
        log_dir = log_config.get('file', {}).get('directory', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        # Create formatters
        json_formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
        text_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        
        # Clear existing handlers
        root_logger.handlers.clear()
        
        # Configure console logging
        if _is_true_value(log_config.get('console', {}).get('enabled', True)):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(
                json_formatter if log_config.get('console', {}).get('format') == 'json' else text_formatter
            )
            console_handler.setLevel(log_level)
            root_logger.addHandler(console_handler)
        
        # Configure file logging
        if _is_true_value(log_config.get('file', {}).get('enabled', True)):
            file_config = log_config['file']
            log_file = os.path.join(log_dir, file_config.get('filename', 'server.log'))
            
            # Set up rotating file handler
            if file_config.get('rotation') == 'midnight':
                file_handler = logging.handlers.TimedRotatingFileHandler(
                    filename=log_file,
                    when='midnight',
                    interval=1,
                    backupCount=file_config.get('backup_count', 30),
                    encoding='utf-8'
                )
            else:
                file_handler = logging.handlers.RotatingFileHandler(
                    filename=log_file,
                    maxBytes=file_config.get('max_size_mb', 10) * 1024 * 1024,
                    backupCount=file_config.get('backup_count', 30),
                    encoding='utf-8'
                )
            
            file_handler.setFormatter(
                json_formatter if file_config.get('format') == 'json' else text_formatter
            )
            file_handler.setLevel(log_level)
            root_logger.addHandler(file_handler)
        
        # Capture warnings if configured
        if _is_true_value(log_config.get('capture_warnings', True)):
            logging.captureWarnings(True)
        
        # Set propagation
        root_logger.propagate = log_config.get('propagate', False)
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("Logging configuration completed")
        
        # Handle verbose setting consistently
        verbose_value = self.config.get('general', {}).get('verbose', False)
        if _is_true_value(verbose_value):
            self.logger.debug("Verbose logging enabled")

    def _create_lifespan_manager(self):
        """
        Create an asynccontextmanager for the FastAPI application lifespan.
        This manages initialization and cleanup of resources.
        
        Returns:
            An asynccontextmanager function for the FastAPI app
        """
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            self.logger.info("Starting up FastAPI application")
            
            # Initialize services and clients
            try:
                await self._initialize_services(app)
                self._log_configuration_summary()
                self.logger.info("Startup complete")
            except Exception as e:
                self.logger.error(f"Failed to initialize services: {str(e)}")
                raise
            
            yield
            
            # Cleanup resources
            try:
                self.logger.info("Shutting down services...")
                await self._shutdown_services(app)
                self.logger.info("Services shut down successfully")
            except Exception as e:
                self.logger.error(f"Error during shutdown: {str(e)}")
        
        return lifespan

    async def _initialize_services(self, app: FastAPI) -> None:
        """
        Initialize all services and clients required by the application.
        
        Args:
            app: The FastAPI application to attach services to
        """
        # Store config in app state
        app.state.config = self.config
        
        # Initialize Chroma client
        chroma_conf = self.config['chroma']
        self.logger.info(f"Connecting to ChromaDB at {chroma_conf['host']}:{chroma_conf['port']}...")
        app.state.chroma_client = chromadb.HttpClient(
            host=chroma_conf['host'],
            port=int(chroma_conf['port'])
        )
        
        # Initialize API Key Service
        app.state.api_key_service = ApiKeyService(self.config)
        self.logger.info("Initializing API Key Service...")
        try:
            await app.state.api_key_service.initialize()
            self.logger.info("API Key Service initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize API Key Service: {str(e)}")
            self.logger.error(f"MongoDB connection details: {self.config.get('mongodb', {})}")
            raise
        
        # Initialize Ollama embeddings
        ollama_conf = self.config['ollama']
        app.state.embeddings = OllamaEmbeddings(
            model=ollama_conf['embed_model'],
            base_url=ollama_conf['base_url']
        )
        
        # Initialize retriever without a collection - it will be set when an API key is provided
        app.state.retriever = ChromaRetriever(None, app.state.embeddings, self.config)
        
        # Initialize GuardrailService
        app.state.guardrail_service = GuardrailService(self.config)
        
        # Load no results message
        no_results_message = self._load_no_results_message()
        
        # Create LLM client with guardrail service
        app.state.llm_client = OllamaClient(
            self.config, 
            app.state.retriever,
            guardrail_service=app.state.guardrail_service,
            no_results_message=no_results_message
        )
        
        app.state.logger_service = LoggerService(self.config)
        
        # Initialize all services concurrently
        init_tasks = [
            app.state.llm_client.initialize(),
            app.state.logger_service.initialize_elasticsearch(),
            app.state.guardrail_service.initialize(),
            app.state.retriever.initialize()
        ]
        
        # Only initialize reranker if enabled
        if _is_true_value(self.config.get('reranker', {}).get('enabled', False)):
            app.state.reranker_service = RerankerService(self.config)
            init_tasks.append(app.state.reranker_service.initialize())
        
        await asyncio.gather(*init_tasks)
        
        # Verify LLM connection
        if not await app.state.llm_client.verify_connection():
            self.logger.error("Failed to connect to Ollama. Exiting...")
            raise Exception("Failed to connect to Ollama")
        
        # Initialize remaining services
        app.state.health_service = HealthService(self.config, app.state.chroma_client, app.state.llm_client)
        app.state.chat_service = ChatService(self.config, app.state.llm_client, app.state.logger_service)

    def _load_no_results_message(self) -> str:
        """
        Load the no results message from a file.
        
        Returns:
            The message to show when no results are found
        """
        try:
            message_file = self.config.get('general', {}).get(
                'no_results_message_file', 
                '../prompts/no_results_message.txt'
            )
            with open(message_file, 'r') as file:
                no_results_message = file.read().strip()
                self.logger.info("Loaded no results message from file")
                return no_results_message
        except Exception as e:
            self.logger.warning(f"Could not load no results message: {str(e)}")
            return "Could not load no results message."

    async def _shutdown_services(self, app: FastAPI) -> None:
        """
        Shut down all services and clients.
        
        Args:
            app: The FastAPI application containing services
        """
        # Close services concurrently
        shutdown_tasks = [
            app.state.llm_client.close(),
            app.state.logger_service.close(),
            app.state.retriever.close(),
            app.state.guardrail_service.close()
        ]
        
        # Only include reranker if it was initialized
        if hasattr(app.state, 'reranker_service'):
            shutdown_tasks.append(app.state.reranker_service.close())
        
        await asyncio.gather(*shutdown_tasks)

    def _log_configuration_summary(self) -> None:
        """Log a summary of the current configuration."""
        self.logger.info("=" * 50)
        self.logger.info("Server initialization complete. Configuration summary:")
        self.logger.info(f"Server running with {self.config['ollama']['model']} model")
        self.logger.info(f"Using ChromaDB collection: {self.config['chroma']['collection']}")
        self.logger.info(f"Confidence threshold: {self.config['chroma'].get('confidence_threshold', 0.85)}")
        
        # Retriever settings
        if hasattr(self.app.state, 'retriever'):
            self.logger.info(f"Relevance threshold: {self.app.state.retriever.relevance_threshold}")
        
        self.logger.info(f"Verbose mode: {_is_true_value(self.config['general'].get('verbose', False))}")
        
        # Reranker settings
        reranker_config = self.config.get('reranker', {})
        reranker_enabled = reranker_config.get('enabled', False)
        
        self.logger.info(f"Reranker: {'enabled' if reranker_enabled else 'disabled'}")
        if reranker_enabled:
            model = reranker_config.get('model', self.config['ollama']['model'])
            top_n = reranker_config.get('top_n', 3)
            
            self.logger.info(f"  Model: {model}")
            self.logger.info(f"  Top N: {top_n} documents")
            self.logger.info(f"  Temperature: {reranker_config.get('temperature', 0.0)}")
        
        # Safety check configuration
        safety_mode = self.config.get('safety', {}).get('mode', 'strict')
        safety_enabled = self.config.get('safety', {}).get('enabled', True)
        self.logger.info(f"Safety service: {'enabled' if safety_enabled else 'disabled'}")
        if safety_enabled:
            self.logger.info(f"Safety check mode: {safety_mode}")
            if safety_mode == 'fuzzy':
                self.logger.info("Using fuzzy matching for safety checks")
            elif safety_mode == 'disabled':
                self.logger.warning("⚠️ Safety checks are disabled - all queries will be processed")
        
        # Log safety configuration
        safety_config = self.config.get('safety', {})
        max_retries = safety_config.get('max_retries', 3)
        retry_delay = safety_config.get('retry_delay', 1.0)
        request_timeout = safety_config.get('request_timeout', 15)
        allow_on_timeout = safety_config.get('allow_on_timeout', False)
        
        if safety_enabled:
            self.logger.info(f"Safety check config: retries={max_retries}, delay={retry_delay}s, timeout={request_timeout}s")
            if allow_on_timeout:
                self.logger.warning("⚠️ Queries will be allowed through if safety check times out")
        
        # Log authenticated services without exposing sensitive info
        auth_services = []
        if (_is_true_value(self.config.get('elasticsearch', {}).get('enabled', False)) and 
                self.config['elasticsearch'].get('auth', {}).get('username')):
            auth_services.append("Elasticsearch")
        if auth_services:
            self.logger.info(f"Authenticated services: {', '.join(auth_services)}")
        self.logger.info("=" * 50)

    def _configure_middleware(self) -> None:
        """Configure middleware for the FastAPI application."""
        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Allows all origins
            allow_credentials=True,
            allow_methods=["*"],  # Allows all methods
            allow_headers=["*"],  # Allows all headers
        )

    def _configure_routes(self) -> None:
        """Configure routes and endpoints for the FastAPI application."""
        # Define dependencies
        async def get_chat_service(request: Request):
            return request.app.state.chat_service

        async def get_health_service(request: Request):
            return request.app.state.health_service

        async def get_guardrail_service(request: Request):
            return request.app.state.guardrail_service

        async def get_api_key_service(request: Request):
            return request.app.state.api_key_service

        async def get_api_key(
            request: Request,
            api_key_service = Depends(get_api_key_service)
        ) -> Optional[str]:
            """
            Extract API key from request headers and validate it
            
            Args:
                request: The incoming request
                api_key_service: The API key service
                
            Returns:
                The collection name associated with the API key
            """
            # Get API key from header
            header_name = self.config.get('api_keys', {}).get('header_name', 'X-API-Key')
            api_key = request.headers.get(header_name)
            
            # Validate API key and get collection name
            try:
                collection_name = await api_key_service.get_collection_for_api_key(api_key)
                return collection_name
            except HTTPException as e:
                # Allow health check without API key if configured
                if (request.url.path == "/health" and 
                    not self.config.get('api_keys', {}).get('require_for_health', False)):
                    return self.config['chroma']['collection']
                raise e

        # Chat endpoint
        @self.app.post("/chat")
        async def chat_endpoint(
            request: Request,
            chat_message: ChatMessage,
            chat_service = Depends(get_chat_service),
            collection_name: str = Depends(get_api_key)
        ):
            """
            Process a chat message and return a response
            
            This endpoint validates the API key and uses the associated collection
            for retrieval augmented generation.
            """
            # Resolve client IP (using X-Forwarded-For if available)
            client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
            
            # Determine if streaming is requested via header or request payload
            stream = (request.headers.get("Accept") == "text/event-stream") or chat_message.stream
            
            # Log the collection being used
            if self.config.get('general', {}).get('verbose', False):
                self.logger.info(f"Using collection '{collection_name}' for chat request from {client_ip}")

            if stream:
                return StreamingResponse(
                    chat_service.process_chat_stream(
                        message=chat_message.message,
                        client_ip=client_ip,
                        collection_name=collection_name
                    ),
                    media_type="text/event-stream"
                )
            else:
                result = await chat_service.process_chat(
                    message=chat_message.message,
                    client_ip=client_ip,
                    collection_name=collection_name
                )
                return result

        # API Key management routes
        @self.app.post("/admin/api-keys", response_model=ApiKeyResponse)
        async def create_api_key(
            api_key_data: ApiKeyCreate,
            api_key_service = Depends(get_api_key_service)
        ):
            """
            Create a new API key associated with a specific collection
            
            This is an admin-only endpoint and should be properly secured in production.
            """
            # In production, add authentication middleware to restrict access to admin endpoints
            
            return await api_key_service.create_api_key(
                api_key_data.collection_name,
                api_key_data.client_name,
                api_key_data.notes
            )

        @self.app.get("/admin/api-keys")
        async def list_api_keys(
            api_key_service = Depends(get_api_key_service)
        ):
            """
            List all API keys
            
            This is an admin-only endpoint and should be properly secured in production.
            """
            # In production, add authentication middleware to restrict access to admin endpoints
            
            try:
                # Ensure service is initialized
                if not api_key_service._initialized:
                    await api_key_service.initialize()
                
                # Retrieve all API keys
                cursor = api_key_service.api_keys_collection.find({})
                api_keys = await cursor.to_list(length=100)  # Limit to 100 keys
                
                # Convert _id to string representation
                for key in api_keys:
                    key["_id"] = str(key["_id"])
                
                return api_keys
                
            except Exception as e:
                self.logger.error(f"Error listing API keys: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to list API keys: {str(e)}")

        @self.app.get("/admin/api-keys/{api_key}/status")
        async def get_api_key_status(
            api_key: str,
            api_key_service = Depends(get_api_key_service)
        ):
            """
            Get the status of a specific API key
            
            This is an admin-only endpoint and should be properly secured in production.
            """
            status = await api_key_service.get_api_key_status(api_key)
            return status

        @self.app.post("/admin/api-keys/deactivate")
        async def deactivate_api_key(
            data: ApiKeyDeactivate,
            api_key_service = Depends(get_api_key_service)
        ):
            """
            Deactivate an API key
            
            This is an admin-only endpoint and should be properly secured in production.
            """
            # In production, add authentication middleware to restrict access to admin endpoints
            
            success = await api_key_service.deactivate_api_key(data.api_key)
            
            if not success:
                raise HTTPException(status_code=404, detail="API key not found")
                
            return {"status": "success", "message": "API key deactivated"}

        # Health check endpoint
        @self.app.get("/health")
        async def health_check(
            health_service = Depends(get_health_service),
            collection_name: str = Depends(get_api_key)
        ):
            """Check the health of the application and its dependencies"""
            health = await health_service.get_health_status(collection_name)
            return health

    def create_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Create an SSL context from certificate and key files.
        
        Returns:
            An SSL context if HTTPS is enabled, None otherwise
        """
        if not _is_true_value(self.config.get('general', {}).get('https', {}).get('enabled', False)):
            return None
        
        try:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(
                certfile=self.config['general']['https']['cert_file'],
                keyfile=self.config['general']['https']['key_file']
            )
            return ssl_context
        except Exception as e:
            self.logger.error(f"Failed to create SSL context: {str(e)}")
            raise

    def run(self) -> None:
        """Run the FastAPI application with the configured settings."""
        # Get server settings from config
        port = int(self.config.get('general', {}).get('port', 3000))
        host = self.config.get('general', {}).get('host', '0.0.0.0')

        # Use HTTPS if enabled in config
        https_enabled = _is_true_value(self.config.get('general', {}).get('https', {}).get('enabled', False))
        
        if https_enabled:
            try:
                ssl_keyfile = self.config['general']['https']['key_file']
                ssl_certfile = self.config['general']['https']['cert_file']
                https_port = int(self.config['general']['https'].get('port', 3443))
                
                self.logger.info(f"Starting HTTPS server on {host}:{https_port}")
                
                # Run without reload option - this is handled by the start script
                uvicorn.run(
                    self.app,
                    host=host,
                    port=https_port,
                    ssl_keyfile=ssl_keyfile,
                    ssl_certfile=ssl_certfile
                )
            except Exception as e:
                self.logger.error(f"Failed to start HTTPS server: {str(e)}")
                import sys
                sys.exit(1)
        else:
            self.logger.info(f"Starting HTTP server on {host}:{port}")
            # Run without reload option - this is handled by the start script
            uvicorn.run(
                self.app,
                host=host,
                port=port
            )

# Create a global app instance for direct use by uvicorn in development mode
app = FastAPI(
    title="Open Inference Server",
    description="A FastAPI server with chat endpoint and RAG capabilities",
    version="1.0.0"
)


# Factory function for creating app instances in multi-worker mode
def create_app() -> FastAPI:
    """
    Factory function to create a FastAPI application instance.
    This is useful for uvicorn's multiple worker mode.
    
    This function looks for a config path in the OIS_CONFIG_PATH environment variable.
    
    Returns:
        A configured FastAPI application
    """
    # Check for config path in environment variables
    config_path = os.environ.get('OIS_CONFIG_PATH')
    
    # Create server instance
    server = InferenceServer(config_path=config_path)
    
    # Return just the FastAPI app instance
    return server.app


# Example of usage directly in this file
if __name__ == "__main__":
    server = InferenceServer()
    server.run()
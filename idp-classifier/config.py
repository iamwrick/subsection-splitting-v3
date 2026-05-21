MODEL_ID = "us.amazon.nova-2-lite-v1:0"
MAX_TOKENS = 5120
TEMPERATURE = 0
MAX_PAGES_PER_CHUNK = 300
AWS_REGION = "us-east-1"

# Amazon Nova 2 Lite pricing (us-east-1, on-demand)
# $0.30  per 1M input tokens
# $2.50  per 1M output tokens
# Prompt caching:
#   Cache write : 1.25× standard input  = $0.375/1M
#   Cache read  : 0.10× standard input  = $0.030/1M  (90% saving)
PRICE_INPUT_PER_1M        = 0.30     # $ per 1M standard input tokens
PRICE_OUTPUT_PER_1M       = 2.50     # $ per 1M output tokens
PRICE_CACHE_WRITE_PER_1M  = 0.375    # $ per 1M cache-write tokens  (1.25×)
PRICE_CACHE_READ_PER_1M   = 0.030    # $ per 1M cache-read tokens   (0.10×)

# Legacy aliases (kept for backwards compat)
PRICE_INPUT_PER_1K  = PRICE_INPUT_PER_1M  / 1000
PRICE_OUTPUT_PER_1K = PRICE_OUTPUT_PER_1M / 1000

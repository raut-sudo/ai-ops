"""Deterministic Faker setup for seed scripts.

All randomness flows through this one seeded instance. This ensures
that run_seed.py produces identical data every execution.

SEED = 42 (fixed globally).
"""

import random

from faker import Faker

SEED = 42

# Configure Faker with the fixed seed
fake = Faker()
Faker.seed(SEED)
random.seed(SEED)

__all__ = ["fake"]

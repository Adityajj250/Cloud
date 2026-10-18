"""
R.I.F.T. Math & Distributed Numerical Worker (Monte Carlo & Matrix Multiplication)
CPU-Optimized with NumPy.
"""

import numpy as np
import time
from typing import Dict, Any

class MathWorker:
    @staticmethod
    def run_monte_carlo(params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Monte Carlo Pi estimation or numerical integration.
        Params:
          seed (int): Random seed for this chunk
          samples (int): Number of points to sample
        """
        seed = int(params.get("seed", 42))
        samples = int(params.get("samples", 1_000_000))
        
        start_time = time.time()
        rng = np.random.default_rng(seed)
        
        # CPU-vectorized 2D random sampling
        x = rng.random(samples, dtype=np.float32)
        y = rng.random(samples, dtype=np.float32)
        inside_circle = np.count_nonzero((x * x + y * y) <= 1.0)
        
        elapsed = time.time() - start_time
        return {
            "worker_type": "monte_carlo",
            "seed": seed,
            "samples": samples,
            "inside_circle": int(inside_circle),
            "estimated_pi": float(4.0 * inside_circle / samples),
            "elapsed_seconds": round(elapsed, 4)
        }

    @staticmethod
    def run_matrix_multiply(params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Row-block matrix multiplication: C_rowblock = A_rowblock @ B
        """
        start_time = time.time()
        row_start = int(params.get("row_start", 0))
        row_end = int(params.get("row_end", 100))
        dim = int(params.get("dim", 500))
        
        # Deterministic generation for benchmarking
        rng_a = np.random.default_rng(seed=123)
        rng_b = np.random.default_rng(seed=456)
        
        # In actual chunk processing, A_rowblock and B are loaded from S3.
        # Here we demonstrate the CPU matrix multiply block
        a_block = rng_a.standard_normal((row_end - row_start, dim), dtype=np.float32)
        b_matrix = rng_b.standard_normal((dim, dim), dtype=np.float32)
        
        c_block = a_block @ b_matrix
        checksum = float(np.sum(c_block))
        elapsed = time.time() - start_time
        
        return {
            "worker_type": "matrix_multiply",
            "row_start": row_start,
            "row_end": row_end,
            "shape": list(c_block.shape),
            "checksum": checksum,
            "elapsed_seconds": round(elapsed, 4)
        }

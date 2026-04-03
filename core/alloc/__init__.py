"""
Adaptive allocation sub-package.

Provides:
  - HRPAllocator: Hierarchical Risk Parity allocation
  - DynamicKellyManager: Equity-momentum-based Kelly switching
  - PnLMatrixBuilder: Utility to build PnL matrices from various sources
"""
from core.alloc.hrp_allocator import HRPAllocator
from core.alloc.kelly_dynamic import DynamicKellyManager
from core.alloc.pnl_matrix_builder import PnLMatrixBuilder

__all__ = ["DynamicKellyManager", "HRPAllocator", "PnLMatrixBuilder"]

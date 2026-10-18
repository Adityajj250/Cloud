"""
R.I.F.T. Image Tile Processing Worker
Handles tiled image transformations (filters, edge detection, resizing) on CPU using Pillow and NumPy.
"""

import time
import io
import base64
from PIL import Image, ImageFilter, ImageOps
from typing import Dict, Any

class ImageWorker:
    @staticmethod
    def process_tile(params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process an image tile.
        Params:
          tile_id: str
          filter_type: 'edge_enhance' | 'grayscale' | 'blur' | 'contour'
          image_b64: optional base64 image chunk, or generated test pattern if omitted
          width, height: tile dimensions
        """
        start_time = time.time()
        filter_type = params.get("filter_type", "edge_enhance")
        tile_id = params.get("tile_id", "tile-0")
        
        # Load image from base64 or create test pattern
        image_b64 = params.get("image_b64")
        if image_b64:
            img_data = base64.b64decode(image_b64)
            img = Image.open(io.BytesIO(img_data)).convert("RGB")
        else:
            w = int(params.get("width", 256))
            h = int(params.get("height", 256))
            img = Image.new("RGB", (w, h), color=(73, 109, 137))

        # Apply CPU image transformation
        if filter_type == "edge_enhance":
            processed = img.filter(ImageFilter.EDGE_ENHANCE_MORE)
        elif filter_type == "contour":
            processed = img.filter(ImageFilter.CONTOUR)
        elif filter_type == "blur":
            processed = img.filter(ImageFilter.GaussianBlur(radius=2))
        elif filter_type == "grayscale":
            processed = ImageOps.grayscale(img)
        else:
            processed = img

        # Save result to buffer
        buf = io.BytesIO()
        processed.save(buf, format="JPEG", quality=85)
        out_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        elapsed = time.time() - start_time

        return {
            "worker_type": "image_tile",
            "tile_id": tile_id,
            "filter_applied": filter_type,
            "output_size_bytes": len(buf.getvalue()),
            "output_b64": out_b64,
            "elapsed_seconds": round(elapsed, 4)
        }

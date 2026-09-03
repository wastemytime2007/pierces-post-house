"""CLIP embedder — generates 512-dim visual embeddings used for similarity search.

CLIP is the magic ingredient: it puts images AND text in the same vector space.
That means later we can embed the phrase "dog running on beach" and find visually
similar B-roll frames even if the VLM never used those exact words.
"""
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from PIL import Image
import open_clip

from .config import CLIP_MODEL_NAME, CLIP_PRETRAINED


class CLIPEmbedder:
    """Loads CLIP once, reuses for all embeddings."""

    def __init__(self, device: Optional[str] = None):
        # Auto-detect best device: CUDA > MPS (Apple Silicon) > CPU
        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        self.device = device

        model, _, preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL_NAME, pretrained=CLIP_PRETRAINED
        )
        self.model = model.to(device).eval()
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(CLIP_MODEL_NAME)

    @torch.no_grad()
    def embed_image(self, image_path: Path) -> np.ndarray:
        """Returns a normalized 512-d embedding as a numpy array."""
        img = Image.open(image_path).convert("RGB")
        tensor = self.preprocess(img).unsqueeze(0).to(self.device)
        features = self.model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().numpy().squeeze()

    @torch.no_grad()
    def embed_images_batch(self, image_paths: list[Path], batch_size: int = 8) -> np.ndarray:
        """Embed multiple images at once (faster). Returns (N, 512) array."""
        all_features = []
        for i in range(0, len(image_paths), batch_size):
            batch = image_paths[i:i + batch_size]
            tensors = torch.stack([
                self.preprocess(Image.open(p).convert("RGB")) for p in batch
            ]).to(self.device)
            features = self.model.encode_image(tensors)
            features = features / features.norm(dim=-1, keepdim=True)
            all_features.append(features.cpu().numpy())
        return np.vstack(all_features)

    @torch.no_grad()
    def embed_text(self, text: str) -> np.ndarray:
        """Embed a text query. Used in stage 3 for matching phrases to clips."""
        tokens = self.tokenizer([text]).to(self.device)
        features = self.model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().numpy().squeeze()

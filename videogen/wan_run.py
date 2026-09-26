"""Run mlx-video's Wan 2.2 generator with a smaller memory footprint.

Same command-line flags as `python -m mlx_video.models.wan_2.generate`. Changes:
- the T5 text encoder keeps its bf16 weights (upstream upcasts all 5.7B parameters to fp32,
  ~23 GB; its attention already computes in fp32, which is what the precision needs);
- MLX's buffer cache is capped, so freed text-encoder and VAE buffers go back to the system
  instead of being held for reuse;
- `--tiling auto` decodes the VAE in 32-frame chunks (upstream: 64), cutting the
  decode peak for a 4 s clip at 960x544 from 42 GB to 31 GB at about the same speed.
"""

import sys

import mlx.core as mx
from mlx_video.models.ltx_2.video_vae.tiling import (
    SpatialTilingConfig,
    TemporalTilingConfig,
    TilingConfig,
)
from mlx_video.models.wan_2 import generate, utils

mx.set_cache_limit(2 * 1024**3)


def load_t5_encoder_bf16(model_path, config):
    from mlx_video.models.wan_2.text_encoder import T5Encoder

    encoder = T5Encoder(vocab_size=config.t5_vocab_size, dim=config.t5_dim, dim_attn=config.t5_dim_attn,
                        dim_ffn=config.t5_dim_ffn, num_heads=config.t5_num_heads,
                        num_layers=config.t5_num_layers, num_buckets=config.t5_num_buckets, shared_pos=False)
    encoder.load_weights(list(mx.load(str(model_path)).items()))
    mx.eval(encoder.parameters())
    return encoder


def balanced_tiling(cls, height, width, num_frames, **_):
    return cls(spatial_config=SpatialTilingConfig(tile_size_in_pixels=512, tile_overlap_in_pixels=64)
               if max(height, width) > 512 else None,
               temporal_config=TemporalTilingConfig(tile_size_in_frames=32, tile_overlap_in_frames=8)
               if num_frames > 33 else None)


utils.load_t5_encoder = generate.load_t5_encoder = load_t5_encoder_bf16
TilingConfig.auto = classmethod(balanced_tiling)

if __name__ == "__main__":
    sys.argv[0] = "wan_run"
    generate.main()

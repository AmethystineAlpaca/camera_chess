"""Infer camera-facing side from the recognizer's square-to-image geometry.
Attention is evidence, not guaranteed localization; uncertain views are rejected.
"""
import numpy as np


def infer_orientation(centroids, width, height):
    points = np.asarray(centroids, dtype=float)
    if points.shape != (64, 2) or not np.isfinite(points).all() or width <= 0 or height <= 0:
        return dict(status='uncertain', detail='Keep the entire board visible and clear your hands.')
    # Semantic queries are in a8..h1 order. Increasing row points toward White's home rank.
    files = np.tile(np.arange(8), 8)
    rows = np.repeat(np.arange(8), 8)
    design = np.column_stack((np.ones(64), files, rows))
    coeff = np.linalg.lstsq(design, points, rcond=None)[0]
    residual = points-design@coeff
    variance = ((points-points.mean(axis=0))**2).sum(axis=0)
    fit = 1-(residual**2).sum(axis=0)/np.maximum(variance,1e-9)
    file_axis = coeff[1]*7*np.array([width,height])
    rank_axis = coeff[2]*7*np.array([width,height])
    rank_length = float(np.linalg.norm(rank_axis))
    file_length = float(np.linalg.norm(file_axis))
    if min(rank_length,file_length) < min(width,height)*.06 or min(fit) < .60:
        return dict(status='uncertain', detail='Board direction is unclear. Keep all squares visible and improve the camera view.', fit=fit.tolist())
    rank_vertical = abs(rank_axis[1])/rank_length
    file_horizontal = abs(file_axis[0])/file_length
    orthogonality = abs(float(np.dot(rank_axis,file_axis)))/(rank_length*file_length)
    if rank_vertical < .90 or file_horizontal < .90 or orthogonality > .45:
        return dict(status='sideways', detail='Turn the board so White or Black faces the camera directly. Do not place it sideways.', fit=fit.tolist())
    white_y = float(points.reshape(8,8,2)[6:,:,1].mean())
    black_y = float(points.reshape(8,8,2)[:2,:,1].mean())
    delta = white_y-black_y
    if abs(delta) < .05 or np.sign(delta) != np.sign(rank_axis[1]):
        return dict(status='uncertain', detail='Both sides are not clearly located. Clear hands and keep the board centered.', fit=fit.tolist())
    return dict(status='detected', near='white' if delta>0 else 'black',
                detail='Board direction detected.', fit=fit.tolist(),
                rank_vertical=float(rank_vertical), file_horizontal=float(file_horizontal),
                separation=abs(delta))


def forward_with_geometry(model, x):
    """Preserve the normal model forward; reconstruct only the last cross-attention.

    A temporary hook reads its Q/K inputs. The ordinary fused inference path and
    logits remain unchanged. Only setup/recovery requests pay this extra cost.
    """
    import torch
    from torch.nn import functional as F
    layer = model.decoder.layers[-1].multihead_attn
    captured = []
    handle = layer.register_forward_pre_hook(lambda _,args:captured.append((args[0],args[1])))
    try:
        logits = model(x)
    finally:
        handle.remove()
    query, memory = captured[0]
    dim, heads = layer.embed_dim, layer.num_heads
    head_dim = dim//heads
    # Small geometry-only work is quicker on CPU than extra non-fused MPS kernels.
    # The model is frozen; keep a detached CPU projection once per loaded model.
    projection = getattr(layer,'_orientation_projection',None)
    if projection is None:
        projection = (layer.in_proj_weight.detach().float().cpu(),
                      layer.in_proj_bias.detach().float().cpu() if layer.in_proj_bias is not None else None)
        layer._orientation_projection = projection
    weights, bias = projection
    q = F.linear(query.float().cpu(),weights[:dim],bias[:dim] if bias is not None else None)
    k = F.linear(memory.float().cpu(),weights[dim:2*dim],bias[dim:2*dim] if bias is not None else None)
    q = q.reshape(x.shape[0],64,heads,head_dim).transpose(1,2)
    k = k.reshape(x.shape[0],-1,heads,head_dim).transpose(1,2)
    attention = (q@k.transpose(-2,-1)/(head_dim**.5)).softmax(-1).mean(1)
    attention = attention[:,:,model.encoder.num_prefix_tokens:]
    attention = attention/attention.sum(-1,keepdim=True).clamp_min(1e-12)
    grid = int(round(attention.shape[-1]**.5))
    if grid*grid != attention.shape[-1]:
        raise ValueError('Unexpected encoder patch layout.')
    xs = (torch.arange(grid).repeat(grid)+.5)/grid
    ys = (torch.arange(grid).repeat_interleave(grid)+.5)/grid
    centers = torch.stack(((attention*xs).sum(-1),(attention*ys).sum(-1)),dim=-1)
    return logits, centers[0].cpu().numpy()

import argparse
import torch
from torch.utils.data import DataLoader
from torchvision.transforms import Compose
from pytorch3d.loss import chamfer_distance
import copy
import open3d as o3d
import numpy as np
import time
from tqdm import tqdm
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))
from hegn.dataloader.transforms import Resampler, RandomJitter, RandomTransformSE3
from hegn.dataloader.dataloader import ModelNetHdf
from scripts.utils.eval_utils import RMSE



def eval(eval_type: str):
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    batch_size = 32

    scale_range = None
    if eval_type == "9dof":
        scale_range = (0.5, 1.5)

    dataset = ModelNetHdf(
        dataset_path="data/modelnet40_ply_hdf5_2048",
        subset="test",
        transform=Compose(
            [
                Resampler(1024),
                RandomTransformSE3(rot_mag=180, trans_mag=0.5, scale_range=scale_range),
                RandomJitter(scale=0.01, clip=0.05),
            ]
        ),
    )
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=False)

    rmse_loss = 0.0
    chamfer_loss = 0.0
    time_per_batch = []
    time_per_sample = []
    for _, batch in enumerate(tqdm(dataloader)):
        x = batch["points"][:, :, :3]  # B x N x 3
        y = batch["points_ts"][:, :, :3]  # B x N x 3

        t_gt = batch["T"].to(device).to(torch.float32)  # B x 3
        R_gt = batch["R"].to(device).to(torch.float32)  # B x 3 x 3
        S_gt = batch["S"].to(device).to(torch.float32)  # B x 3 x 3

        curr_batch_size = x.size(0)

        R_pred = []
        S_pred = []
        t_pred = []

        x_aligned = []

        for j in range(curr_batch_size):
            source_pc = o3d.geometry.PointCloud()
            source_pc.points = o3d.utility.Vector3dVector(x[j].numpy())
            target_pc = o3d.geometry.PointCloud()
            target_pc.points = o3d.utility.Vector3dVector(y[j].numpy())

            start_time = time.time()
            reg_p2p = o3d.pipelines.registration.registration_icp(
                source_pc,
                target_pc,
                0.02,
                np.eye(4),
                o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            )
            time_per_sample.append(time.time() - start_time)

            # Extract transformations
            R = (
                torch.tensor(reg_p2p.transformation[:3, :3])
                .to(device)
                .to(torch.float32)
            )
            t = torch.tensor(reg_p2p.transformation[:3, 3]).to(device).to(torch.float32)
            S = torch.eye(3).to(device).to(torch.float32)  # Assume no scaling

            R_pred.append(R)
            S_pred.append(S)
            t_pred.append(t)

            source_pc_aligned = copy.deepcopy(source_pc)
            source_pc_aligned = source_pc_aligned.transform(reg_p2p.transformation)
            source_pc_aligned = (
                torch.tensor(np.array(source_pc_aligned.points))
                .to(device)
                .to(torch.float32)
            )
            x_aligned.append(source_pc_aligned)

        time_per_batch.append(sum(time_per_sample[-curr_batch_size:]))

        R_pred = torch.stack(R_pred).to(device).to(torch.float32)  # B x 3 x 3
        S_pred = torch.stack(S_pred).to(device).to(torch.float32)  # B x 3 x 3
        t_pred = torch.stack(t_pred).to(device).to(torch.float32)  # B x 3

        x_aligned = torch.stack(x_aligned).to(device).to(torch.float32)  # B x N x 3

        rmse_loss += RMSE(
            x_aligned.transpose(2, 1),
            R_pred,
            S_pred,
            t_pred.unsqueeze(-1),
            R_gt,
            S_gt,
            t_gt,
        )
        chamfer_loss += chamfer_distance(x_aligned, y.to(device).to(torch.float32))[0].mean()

    print(f"ICP in {eval_type} results:")
    print(f"RMSE: {rmse_loss / len(dataloader)}")
    print(f"Chamfer Loss: {chamfer_loss / len(dataloader)}")
    print(
        f"Average time per batch: {sum(time_per_batch)/len(time_per_batch)}, FPS: {1/(sum(time_per_batch)/len(time_per_batch))}"
    )
    print(f"Max time per batch: {max(time_per_batch)}")
    print(f"Min time per batch: {min(time_per_batch)}")
    print(
        f"Average time per sample: {sum(time_per_sample)/len(time_per_sample)}, FPS: {1/(sum(time_per_sample)/len(time_per_sample))}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        type=str,
        default="6dof",
        help="Either 6dof or 9dof evaluation (default: 6dof)",
        choices=["6dof", "9dof"],
    )
    eval(eval_type=parser.parse_args().s)
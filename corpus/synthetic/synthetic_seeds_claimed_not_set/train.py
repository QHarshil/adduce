import torch

model = torch.nn.Linear(4, 2)


def loss(batch):
    return model(batch).sum()

import torch

torch.backends.cuda.matmul.allow_tf32 = True
model = torch.nn.Linear(8, 2)

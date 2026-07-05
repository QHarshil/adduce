import cv2
import numpy as np
import yaml


def load(path: str):
    with open(path) as handle:
        config = yaml.safe_load(handle)
    image = cv2.imread(config["image"])
    return np.asarray(image)

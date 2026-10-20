import numpy as np


def main() -> None:
    order = np.random.permutation(10)
    print(order.tolist())


if __name__ == "__main__":
    main()

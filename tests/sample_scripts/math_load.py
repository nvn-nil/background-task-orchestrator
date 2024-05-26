import sys
import math
import time


def main():
    print("Running task with argv:", sys.argv[1:])

    start = time.perf_counter()
    counter = 0
    while True:
        a = math.sqrt(64*64*64*64*64*64)

        if (time.perf_counter() - start) > 30:
            break

        counter += 1

    exit(0)

if __name__ == "__main__":
    main()

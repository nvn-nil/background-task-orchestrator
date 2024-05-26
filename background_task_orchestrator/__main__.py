from contextlib import contextmanager
import logging
import json
import subprocess
import psutil
import time
import threading
import os
import sys
from argparse import ArgumentParser
from collections import deque
import signal
from datetime import datetime


logger = logging.getLogger(__name__)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stdout_handler.setFormatter(formatter)
logger.addHandler(stdout_handler)

processes = deque()
terminated_indices = deque()
completed_indices = deque()
suspended_processes = deque()


def popen_and_call(command, index, on_start, on_finish, *popen_args, **popen_kwargs):
    """
    Runs the given args in a subprocess.Popen, and then calls the function
    on_exit when the subprocess completes.
    on_exit is a callable object, and popen_args is a list/tuple of args that
    would give to subprocess.Popen.
    """
    def run_in_process(on_start, on_finish, *popen_args, **popen_kwargs):
        proc = subprocess.Popen(command, *popen_args, **popen_kwargs)
        
        if callable(on_start):
            on_start(proc, index)
 
        proc.index = index
        proc.wait()
 
        if callable(on_finish):
            on_finish(proc, index)
        
        return

    monitor_thread = threading.Thread(target=run_in_process, args=(on_start, on_finish, *popen_args), kwargs=popen_kwargs)
    monitor_thread.daemon = True
    monitor_thread.start()
    logger.debug("Spawned thread to handle process with index %s", index)
    return monitor_thread


def load_inputs(input_file):
    with open(input_file) as fi:
        data = json.load(fi)

    if not isinstance(data, list):
        raise Exception("Input json must be an array")
    
    for item in data:
        assert isinstance(item.get("args", []), list)
        assert isinstance(item.get("kwargs", {}), dict)

    return data


def spawn_task(idx, inputs, run_script):
    args_str = " ".join(list(map(str, inputs.get("args", []))))
    kwargs_str = " ".join([f"--{k} {v}" for k, v in inputs.get("kwargs", {}).items()])

    def on_start(process, index):
        processes.append(process)
        logger.debug("on_start of process with index %s", index)

    def on_finish(process, index):
        index_to_delete = [i for i, proc in enumerate(processes) if index == proc.index]
        if index_to_delete:
            del processes[index_to_delete[0]]

        if process.returncode == 0:
            completed_indices.append(index)
            logger.debug("Process with index %s returned 0 exitcode, marking complete", index)
        else:
            logger.debug("Process with index %s returned non 0 exitcode, assuming incomplete", index)

        logger.debug("on_finish of process with index %s", index)

    cmd = run_script.replace(r"\{\s*args\s*\}", args_str).replace(r"\{\s*kwargs\s*\}", kwargs_str)
    logger.debug("Prepared command for index %s is : %s", idx, cmd)
    popen_and_call(
        cmd,
        idx,
        on_start,
        on_finish,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    logger.info("Spawned process with index %s", idx)


def kill_task():
    if processes:
        process = processes.pop()
        process.terminate()
        logger.info("Killed process with index %s", process.index)


def suspend_task():
    has_sigstop = hasattr(signal, "SIGSTOP")

    if processes:
        process = processes.pop()
        logger.debug("Suspending process with index %s", process.index)
        
        if has_sigstop:
            logger.debug("Sending process signal.SIGSTOP signal")
            process.send_signal(signal.SIGSTOP)
        else:
            logger.debug("Using psutil to suspend process")
            p = psutil.Process(process.pid)
            p.suspend()
        
        logger.info("Suspended process with index %s", process.index)
        suspended_processes.append(process)


def continue_suspened_task():
    has_sigstop = hasattr(signal, "SIGSTOP")
    
    if suspended_processes:
        process = suspended_processes.popleft()
        logger.debug("Continuing process with index %s", process.index)
            
        if has_sigstop:
            logger.debug("Sending process signal.SIGCONT signal")
            process.send_signal(signal.SIGCONT)
        else:
            logger.debug("Using psutil to continue process")
            p = psutil.Process(process.pid)
            p.resume()
            
        logger.info("Continued process with index %s", process.index)
        processes.append(process)


@contextmanager
def user_flow():
    started_datetime = datetime.now()
    try:
        yield
    except KeyboardInterrupt:
        logger.info("Killing %s running processes and %s suspended processes", len(processes), len(suspended_processes))
        for process in processes:
            process.terminate()

        for process in suspended_processes:
            process.terminate()
    except Exception:
        raise
    else:
        logger.info("All inputs processed. Exiting..")
    finally:
        with open(f"run_{started_datetime.isoformat().replace(':', '-')}.json", "w") as fo:
            data = {
                "running_indices": [proc.index for proc in list(processes)],
                "terminated_indices": list(terminated_indices),
                "completed_indices": list(completed_indices),
                "suspended_indices": [proc.index for proc in list(suspended_processes)]
            }
            json.dump(data, fo, indent=2)


def main():
    parser = ArgumentParser()
    parser.add_argument("--input-json", type=str, required=True)
    parser.add_argument("--run-script", type=str, required=True)
    parser.add_argument("--target-cpu-utilization", type=int, required=False, default=80)
    parser.add_argument("--max-processes", type=int, required=False, default=os.cpu_count() - 2)
    parser.add_argument('--kill-tasks', dest='kill_tasks', default=False, action='store_true')
    parser.add_argument("--verbose", dest="verbose", default=False, action="store_true")
    args = parser.parse_args()

    inputs = load_inputs(args.input_json)
    input_indices = [i for i in range(len(inputs))]

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    with user_flow():
        logger.debug("Entering user flow")
        while len(set(completed_indices)) != len(inputs):
            logger.debug("Entering main loop")

            unique_completed_indices = set(completed_indices)
            logger.debug("unique_completed_indices: %s", unique_completed_indices)

            # This actually takes 'interval' amount of time, so use this instead of a sleep timer
            cpu_utilization = psutil.cpu_percent(interval=0.15)
            logger.debug("cpu_utilization %s", cpu_utilization)

            cpu_per_logical_core = 100 / os.cpu_count()
            cpu_available = (cpu_utilization <= args.target_cpu_utilization) and (cpu_utilization + cpu_per_logical_core) < args.target_cpu_utilization
            cpu_over_utilized = cpu_utilization > args.target_cpu_utilization
            processes_allowed = len(processes) < args.max_processes
            inputs_complete = set(sorted(unique_completed_indices)) == set(input_indices)
            inputs_processing = [proc.index for proc in list(processes)]
            has_suspened_processes = len(list(suspended_processes))

            if inputs_complete:
                logger.info("All tasks complete, exiting main loop")
                break            
            
            input_index = list(set(input_indices) - set(completed_indices) - set(inputs_processing))[0]
            logger.debug("Next task index: %s", input_index)


            if cpu_available and has_suspened_processes:
                logger.debug("Continuing suspended task")
                continue_suspened_task()
                time.sleep(2)
                logger.info("Continued suspended task")
            elif  cpu_available and processes_allowed:
                logger.debug("Spawning new task with input_index: %s", input_index)
                spawn_task(input_index, inputs[input_index], args.run_script)
                time.sleep(2)
            elif cpu_over_utilized and processes:
                if args.kill_tasks:
                    logger.debug("Killing running task")
                    kill_task()
                else:
                    logger.debug("Suspending running task")
                    suspend_task()

                time.sleep(2)

            logger.info("Running tasks: %s, CPU util: %s, completed tasks: %s", len(processes), f"{cpu_utilization}%", len(completed_indices))
    

if __name__ == "__main__":
    main()
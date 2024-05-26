## Background Task Orchestrator

Automatically loads and unloads CPU using tasks as not to choke other more high priority processes.

Given a set of inputs, the tool will run new processes if the cpu utilization is less than a threshold. This will happen till a set CPU threshold.
As other processes use CPU, the tool will kill/suspend one of its own processes to bring the CPU utilization back withing the threshold. This will
effectively ramp down process count as other processes ramp up CPU utilization.

Planned:
- Memory threshold
- Priority, run two instances with one having higher priority than the other. Kill/suspend tasks from the lower priority queue first

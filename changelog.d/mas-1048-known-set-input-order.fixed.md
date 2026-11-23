- **Two assignment runs of one sample read the reference lists' lines in the
  same order.** A run's known set was ordered by the reference ions' ids,
  which are minted at random in each server process, so two runs of an
  unchanged sample recorded their envelope observations in different orders,
  and any step that keeps the first of two equals could choose differently.
  The ions now keep the order the reference expansion gives them.

"""
Algorithmic diff and reorder engine using Longest Increasing Subsequence (LIS)
for minimal YouTube API quota consumption.
"""

import bisect


def find_lis_indices(arr):
    """
    Computes the set of indices corresponding to a Longest Increasing Subsequence in arr.
    Uses O(N log N) patience sorting with predecessor tracking.
    """
    n = len(arr)
    if n == 0:
        return set()

    tails_val = []
    tails_idx = []
    parent = [-1] * n

    for i, x in enumerate(arr):
        idx = bisect.bisect_left(tails_val, x)
        if idx == len(tails_val):
            tails_val.append(x)
            tails_idx.append(i)
        else:
            tails_val[idx] = x
            tails_idx[idx] = i
        if idx > 0:
            parent[i] = tails_idx[idx - 1]

    lis_idx = set()
    curr = tails_idx[-1]
    while curr != -1:
        lis_idx.add(curr)
        curr = parent[curr]
    return lis_idx


def compute_minimal_moves(curr_list, target_list):
    """
    Calculates the sequence of minimal move operations to transform curr_list into target_list.
    curr_list and target_list must contain unique identifiers (e.g. playlistItemIds).
    Returns a list of tuples: (item_identifier, target_position).
    """
    curr = list(curr_list)
    t_pos = {v: i for i, v in enumerate(target_list)}
    moves = []

    while curr != target_list:
        arr = [t_pos[v] for v in curr]
        lis_idx = find_lis_indices(arr)
        candidates = [i for i in range(len(curr)) if i not in lis_idx]
        if not candidates:
            break

        best_i = candidates[0]
        best_gain = -1

        for i in candidates:
            elem = curr[i]
            des = t_pos[elem]
            temp = list(curr)
            temp.pop(i)
            temp.insert(des, elem)
            temp_arr = [t_pos[v] for v in temp]
            temp_lis_len = len(find_lis_indices(temp_arr))
            if temp_lis_len > best_gain:
                best_gain = temp_lis_len
                best_i = i

        elem = curr[best_i]
        des = t_pos[elem]
        curr.pop(best_i)
        curr.insert(des, elem)
        moves.append((elem, des))

    return moves



# -*- coding: utf-8 -*-
"""
Created on Tue Aug 20 09:32:04 2019

@author: hu_xk
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from imblearn.ensemble import BalancedRandomForestClassifier
from sklearn.ensemble import RandomForestClassifier
from sod_common.paths import DATA_DIR as PROJECT_DATA_DIR

DATA_DIR = PROJECT_DATA_DIR / "entrance_tagging"

def get_distance(dis_array,start_index,end_index):
    if start_index> end_index:
         a = start_index;
         start_index = end_index;
         end_index = a;            
    if start_index == end_index:
        return 0;
    temp_dis1 = sum(dis_array[start_index:end_index]);
    temp_dis2 = sum(dis_array[end_index:len(dis_array)])+sum(dis_array[0:start_index]);
    return min(temp_dis1,temp_dis2);

def distance_error(dis_array, entrance_index, probs):
    dis_error = 0;
    bb = probs.tolist()
    index = bb.index(max(bb))
    dis_error = get_distance(dis_array,int(entrance_index),index);
    bb.sort(reverse = True)
    estimated_rank = bb.index(probs[int(entrance_index)])
    return dis_error,index,estimated_rank

def build_models():
    """WRF and BRF, the two best models in the paper (SmoteBoost is not used)."""
    return {
        'wrf': RandomForestClassifier(n_estimators=80, max_depth=12, random_state=0, class_weight={0: 1, 1: 160}),
        'brf': BalancedRandomForestClassifier(n_estimators=100, max_depth=14, random_state=0),
    }

def evaluate(model, data_dir, groups):
    """Train on each group's training set and tag its test buildings.

    Returns one row per test building: estimated index, true index, entrance x/y,
    number of samples, distance error, rank of the true sample and normalized rank.
    """
    saved_data = []
    for group in range(1, groups+1):
        group_dir = data_dir / str(group)
        raw_data = pd.read_csv(group_dir / 'test_rf_head.csv', header=None).values
        test_list = raw_data[0]
        test_truth = raw_data[1]
        test_candidate_len = raw_data[2]
        test_entrances_xs = raw_data[3]
        test_entrances_ys = raw_data[4]
        target_name = 'class'
        T = pd.read_csv(group_dir / 'features.csv')
        head = list(T.columns)
        head.remove(target_name)
        model.fit(T[head].to_numpy(), T[target_name].to_numpy())
        for i in range(len(test_list)):
            dis_array = raw_data[5:5+int(test_candidate_len[i]), i]
            test_table = pd.read_csv(group_dir / (str(int(test_list[i]))+'rftest.csv'))
            prob = model.predict_proba(test_table[head].to_numpy())
            cur_dis_error, estimated_index, rank = distance_error(dis_array, test_truth[i], prob[:, 1])
            saved_data.append([estimated_index, test_truth[i], test_entrances_xs[i], test_entrances_ys[i],
                               len(dis_array), cur_dis_error, rank+1, float(rank+1)/len(dis_array)])
    return saved_data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-validate WRF and BRF entrance tagging.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Directory holding the group folders.")
    parser.add_argument("--groups", type=int, default=5, help="Number of cross-validation groups.")
    args = parser.parse_args()
    for name, model in build_models().items():
        saved_data = evaluate(model, args.data_dir, args.groups)
        total_errors = [row[5] for row in saved_data]
        print(len(total_errors))
        print('total_error for '+name+': '+str(np.average(total_errors)))
        pd.DataFrame(saved_data).to_csv(args.data_dir / ('tagging_result_'+name+'.csv'))

"""
Created on Tue Aug 20 09:32:04 2019

@author: hu_xk
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from training_tagging import DATA_DIR, build_models, evaluate


def cdf_plot(data, name, number):
    """
    data: list of distance errors
    name: label shown in the legend
    number: number of points between the minimum and maximum of the data
    """
    ecdf = sm.distributions.ECDF(data)
    x = np.linspace(min(data), max(data), number)
    y = ecdf(x)
    plt.xlabel('distance error (m)')
    plt.ylabel('accuracy')
    plt.grid()
    plt.xticks(np.arange(min(data), max(data)+1, 20))
    plt.plot(x, y, label=name,linewidth=4)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plot the distance-error CDF of WRF and BRF.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Directory holding the group folders.")
    parser.add_argument("--groups", type=int, default=5, help="Number of cross-validation groups.")
    parser.add_argument("--no-show", action="store_true", help="Save the plot to <data-dir>/error_cdf.png instead of opening a window.")
    args = parser.parse_args()
    for name, model in build_models().items():
        total_errors = [row[5] for row in evaluate(model, args.data_dir, args.groups)]
        print('total_error for '+name+': '+str(np.average(total_errors)))
        cdf_plot(total_errors, name, 100)
        pd.DataFrame(total_errors).to_csv(args.data_dir / ('distance_errors_'+name+'.csv'))
    plt.legend()
    if args.no_show:
        plt.savefig(args.data_dir / 'error_cdf.png')
    else:
        plt.show()

from __future__ import absolute_import, division, print_function

import os
import numpy as np
import matplotlib.pyplot as plt
import json
from PIL import Image

from ..datasets import OTB
from ..utils.metrics import rect_iou, center_error
from ..utils.viz import show_frame


class ExperimentOTB(object):
    r"""Experiment pipeline and evaluation toolkit for OTB dataset.
    
    Args:
        root_dir (string): Root directory of OTB dataset.
        version (integer or string): Specify the benchmark version, specify as one of
            ``2013``, ``2015``, ``tb50`` and ``tb100``. Default is ``2015``.
        result_dir (string, optional): Directory for storing tracking
            results. Default is ``./results``.
        report_dir (string, optional): Directory for storing performance
            evaluation results. Default is ``./reports``.
    """
    def __init__(self, root_dir, version=2015,
                 result_dir='results', report_dir='reports'):
        super(ExperimentOTB, self).__init__()
        self.dataset = OTB(root_dir, version, download=True)
        self.result_dir = os.path.join(result_dir, 'OTB' + str(version))
        self.report_dir = os.path.join(report_dir, 'OTB' + str(version))
        # as nbins_iou increases, the success score
        # converges to the average overlap (AO)
        self.nbins_iou = 21
        self.nbins_ce = 51

    def run(self, tracker, visualize=False):
        print('Running tracker %s on %s...' % (
            tracker.name, type(self.dataset).__name__))

        # loop over the complete dataset
        for s, (img_files, anno) in enumerate(self.dataset):
            seq_name = self.dataset.seq_names[s]
            print('--Sequence %d/%d: %s' % (s + 1, len(self.dataset), seq_name))

            # skip if results exist
            record_file = os.path.join(
                self.result_dir, tracker.name, '%s.txt' % seq_name)
            if os.path.exists(record_file):
                print('  Found results, skipping', seq_name)
                continue

            # tracking loop
            boxes, times = tracker.track(
                img_files, anno[0, :], visualize=visualize)
            assert len(boxes) == len(anno)
            
            # record results
            self._record(record_file, boxes, times)

    def report(self, tracker_names):
        assert isinstance(tracker_names, (list, tuple))

        # assume tracker_names[0] is your tracker
        report_dir = os.path.join(self.report_dir, tracker_names[0])
        if not os.path.isdir(report_dir):
            os.makedirs(report_dir)
        report_file = os.path.join(report_dir, 'performance.json')

        performance = {}
        for name in tracker_names:
            seq_num = len(self.dataset)
            succ_curve = np.zeros((seq_num, self.nbins_iou))
            prec_curve = np.zeros((seq_num, self.nbins_ce))
            speeds = np.zeros(seq_num)

            performance.update({name: {
                'overall': {},
                'seq_wise': {}}})

            for s, (_, anno) in enumerate(self.dataset):
                seq_name = self.dataset.seq_names[s]
                record_file = os.path.join(
                    self.result_dir, name, '%s.txt' % seq_name)
                boxes = np.loadtxt(record_file, delimiter=',')
                boxes[0] = anno[0]
                assert len(boxes) == len(anno)

                ious = rect_iou(boxes, anno)
                center_errors = center_error(boxes, anno)
                succ_curve[s], prec_curve[s] = self._calc_curves(ious, center_errors)

                # calculate average tracking speed
                time_file = os.path.join(
                    self.result_dir, name, 'times/%s_time.txt' % seq_name)
                if os.path.isfile(time_file):
                    times = np.loadtxt(time_file)
                    times = times[times > 0]
                    if len(times) > 0:
                        speeds[s] = np.mean(1. / times)

                # store sequence-wise performance
                performance[name]['seq_wise'].update({seq_name: {
                    'success_curve': succ_curve[s].tolist(),
                    'precision_curve': prec_curve[s].tolist(),
                    'success_score': np.mean(succ_curve[s]),
                    'precision_score': prec_curve[s][20],
                    'success_rate': succ_curve[s][self.nbins_iou // 2],
                    'speed_fps': speeds[s] if speeds[s] > 0 else -1}})

            succ_curve = np.mean(succ_curve, axis=0)
            prec_curve = np.mean(prec_curve, axis=0)
            succ_score = np.mean(succ_curve)
            prec_score = prec_curve[20]
            succ_rate = succ_curve[self.nbins_iou // 2]
            if np.count_nonzero(speeds) > 0:
                avg_speed = np.sum(speeds) / np.count_nonzero(speeds)
            else:
                avg_speed = -1

            # store overall performance
            performance[name]['overall'].update({
                'success_curve': succ_curve.tolist(),
                'precision_curve': prec_curve.tolist(),
                'success_score': succ_score,
                'precision_score': prec_score,
                'success_rate': succ_rate,
                'speed_fps': avg_speed})

        # report the performance
        with open(report_file, 'w') as f:
            json.dump(performance, f, indent=4)
        # plot precision and success curves
        self._plot_curves(performance, report_dir)

        return performance

    def show(self, tracker_names, seq_names=None, play_speed=1):
        if seq_names is None:
            seq_names = self.dataset.seq_names
        elif isinstance(seq_names, str):
            seq_names = [seq_names]
        assert isinstance(tracker_names, (list, tuple))
        assert isinstance(seq_names, (list, tuple))

        play_speed = int(round(play_speed))
        assert play_speed > 0

        for s, seq_name in enumerate(seq_names):
            print('[%d/%d] Showing results on %s...' % (
                s + 1, len(seq_names), seq_name))
            
            # load all tracking results
            records = {}
            for name in tracker_names:
                record_file = os.path.join(
                    self.result_dir, name, '%s.txt' % seq_name)
                records[name] = np.loadtxt(record_file, delimiter=',')
            
            # loop over the sequence and display results
            img_files, anno = self.dataset[seq_name]
            for f, img_file in enumerate(img_files):
                if not f % play_speed == 0:
                    continue
                image = Image.open(img_file)
                boxes = [anno[f]] + [
                    records[name][f] for name in tracker_names]
                show_frame(image, boxes,
                           legends=['GroundTruth'] + tracker_names,
                           colors=['w', 'r', 'g', 'b', 'c', 'm', 'y',
                                   'orange', 'purple', 'brown', 'pink'])

    def _record(self, record_file, boxes, times):
        # record bounding boxes
        record_dir = os.path.dirname(record_file)
        if not os.path.isdir(record_dir):
            os.makedirs(record_dir)
        np.savetxt(record_file, boxes, fmt='%.3f', delimiter=',')
        print('  Results recorded at', record_file)

        # record running times
        time_dir = os.path.join(record_dir, 'times')
        if not os.path.isdir(time_dir):
            os.makedirs(time_dir)
        time_file = os.path.join(time_dir, os.path.basename(
            record_file).replace('.txt', '_time.txt'))
        np.savetxt(time_file, times, fmt='%.8f')

    def _calc_curves(self, ious, center_errors):
        ious = np.asarray(ious, float)[:, np.newaxis]
        center_errors = np.asarray(center_errors, float)[:, np.newaxis]

        thr_iou = np.linspace(0, 1, self.nbins_iou)[np.newaxis, :]
        thr_ce = np.arange(0, self.nbins_ce)[np.newaxis, :]

        bin_iou = np.greater(ious, thr_iou)
        bin_ce = np.less_equal(center_errors, thr_ce)

        succ_curve = np.mean(bin_iou, axis=0)
        prec_curve = np.mean(bin_ce, axis=0)

        return succ_curve, prec_curve

    def _plot_curves(self, performance, report_dir):
        if not os.path.isdir(report_dir):
            os.makedirs(report_dir)
        succ_file = os.path.join(report_dir, 'success_plots.png')
        prec_file = os.path.join(report_dir, 'precision_plots.png')
        key = 'overall'

        # plot success curves
        thr_iou = np.linspace(0, 1, self.nbins_iou)
        fig, ax = plt.subplots()
        lines = []
        legends = []
        for name, perf in performance.items():
            line, = ax.plot(thr_iou, perf[key]['success_curve'])
            lines.append(line)
            legends.append('%s: [%.3f]' % (name, perf[key]['success_score']))
        ax.legend(lines, legends, loc=1)
        ax.set(xlabel='Overlap threshold', ylabel='Success rate',
               xlim=(0, 1), ylim=(0, None), title='Success plots of OPE')
        
        print('Saving success plots to', succ_file)
        fig.savefig(succ_file, dpi=300)

        # plot precision curves
        thr_ce = np.arange(0, self.nbins_ce)
        fig, ax = plt.subplots()
        lines = []
        legends = []
        for name, perf in performance.items():
            line, = ax.plot(thr_ce, perf[key]['precision_curve'])
            lines.append(line)
            legends.append('%s: [%.3f]' % (name, perf[key]['precision_score']))
        ax.legend(lines, legends, loc=2)
        ax.set(xlabel='Location error threshold', ylabel='Precision',
               xlim=(0, 50), ylim=(0, None), title='Precision plots of OPE')
        
        print('Saving precision plots to', prec_file)
        fig.savefig(prec_file, dpi=300)

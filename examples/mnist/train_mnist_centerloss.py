# MIT License
#
# Copyright (c) 2018 Haoxintong
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
""""""
import os
import time
import mxnet as mx
import numpy as np

from gluonfr.loss import CenterLoss
from mxnet.gluon.data.vision import MNIST
from mxnet import nd, gluon, metric as mtc, autograd as ag
from gluoncv.utils import LRScheduler

from examples.mnist.net.mnist_net import MnistNet
from examples.mnist.utils import plot_result, transform_val, transform_train

os.environ['MXNET_GLUON_REPO'] = 'https://apache-mxnet.s3.cn-north-1.amazonaws.com.cn/'
os.environ['MXNET_ENABLE_GPU_P2P'] = '0'


def validate(net, val_data, ctx, loss, plot=False):
    metric = mtc.Accuracy()
    val_loss = 0
    ebs = []
    lbs = []
    for i, batch in enumerate(val_data):
        data = gluon.utils.split_and_load(batch[0], ctx_list=ctx, batch_axis=0, even_split=False)
        labels = gluon.utils.split_and_load(batch[1], ctx_list=ctx, batch_axis=0, even_split=False)

        ots = [net(X) for X in data]
        embedds = [ot[0] for ot in ots]
        outputs = [ot[1] for ot in ots]
        losses = [loss(yhat, y, emb) for yhat, y, emb in zip(outputs, labels, embedds)]

        metric.update(labels, outputs)
        val_loss += sum([l.mean().asscalar() for l in losses]) / len(losses)
        if plot:
            for es, ls in zip(embedds, labels):
                assert len(es) == len(ls)
                for idx in range(len(es)):
                    ebs.append(es[idx].asnumpy())
                    lbs.append(ls[idx].asscalar())
    if plot:
        ebs = np.vstack(ebs)
        lbs = np.hstack(lbs)

    _, val_acc = metric.get()
    return val_acc, val_loss / len(val_data), ebs, lbs


def train():
    epochs = 101

    lr = 0.1

    momentum = 0.9
    wd = 5e-4

    plot_period = 20

    ctx = [mx.gpu(i) for i in range(2)]
    batch_size = 256

    train_set = MNIST(train=True, transform=transform_train)
    train_data = gluon.data.DataLoader(train_set, batch_size, True, num_workers=4, last_batch='discard')
    val_set = MNIST(train=False, transform=transform_val)
    val_data = gluon.data.DataLoader(val_set, batch_size, shuffle=False, num_workers=4)

    net = MnistNet(embedding_size=2)
    net.initialize(init=mx.init.MSRAPrelu(), ctx=ctx)
    net.hybridize()

    loss = CenterLoss(10, 2, 1)
    loss.initialize(ctx=ctx)

    num_batches = len(train_set) // batch_size
    train_params = net.collect_params()
    train_params.update(loss.params)

    lr_scheduler = LRScheduler("cosine", lr,  niters=num_batches, nepochs=epochs, targetlr=1e-8,
                               warmup_epochs=10, warmup_lr=0.001)
    trainer = gluon.Trainer(train_params, 'nag', {'lr_scheduler': lr_scheduler, 'momentum': momentum, 'wd': wd})

    metric = mtc.Accuracy()
    num_batch = len(train_data)

    for epoch in range(epochs):

        plot = True if (epoch % plot_period) == 0 else False

        train_loss = 0
        metric.reset()
        tic = time.time()
        ebs, lbs = [], []

        for i, batch in enumerate(train_data):
            data = gluon.utils.split_and_load(batch[0], ctx_list=ctx, batch_axis=0, even_split=False)
            labels = gluon.utils.split_and_load(batch[1], ctx_list=ctx, batch_axis=0, even_split=False)

            with ag.record():
                ots = [net(X) for X in data]
                embedds = [ot[0] for ot in ots]
                outputs = [ot[1] for ot in ots]
                losses = [loss(yhat, y, emb) for yhat, y, emb in zip(outputs, labels, embedds)]

            for l in losses:
                ag.backward(l)

            if plot:
                for es, ls in zip(embedds, labels):
                    assert len(es) == len(ls)
                    for idx in range(len(es)):
                        ebs.append(es[idx].asnumpy())
                        lbs.append(ls[idx].asscalar())

            lr_scheduler.update(i, epoch)
            trainer.step(batch_size)
            metric.update(labels, outputs)

            train_loss += sum([l.mean().asscalar() for l in losses]) / len(losses)

        _, train_acc = metric.get()
        train_loss /= num_batch

        val_acc, val_loss, val_ebs, val_lbs = validate(net, val_data, ctx, loss, plot)

        toc = time.time()
        print('[epoch % 3d] train accuracy: %.6f, train loss: %.6f | '
              'val accuracy: %.6f, val loss: %.6f, time: %.6f'
              % (epoch, train_acc, train_loss, val_acc, val_loss, toc - tic))

        if plot:
            ebs, lbs = np.vstack(ebs), np.hstack(lbs)

            plot_result(ebs, lbs, os.path.join("../../resources", "center-train-epoch{}.png".format(epoch)))
            plot_result(val_ebs, val_lbs, os.path.join("../../resources", "center-val-epoch{}.png".format(epoch)))


if __name__ == '__main__':
    train()

"""Few-Shot_learning Segmentation"""

import numpy as np
import torch
import torch.nn as nn
from nn_common_modules import modules as sm
from data_utils import split_batch
import torch.nn.functional as F


class SDnetConditioner(nn.Module):
    """
    A conditional branch of few shot learning regressing the parameters for the segmentor
    """

    def __init__(self, params):
        super(SDnetConditioner, self).__init__()
        params['num_channels'] = 2
        params['num_filters'] = 16
        self.encode1 = sm.SDnetEncoderBlock(params)

        params['num_channels'] = 16
        self.encode2 = sm.SDnetEncoderBlock(params)

        self.encode3 = sm.SDnetEncoderBlock(params)

        self.encode4 = sm.SDnetEncoderBlock(params)

        self.bottleneck = sm.GenericBlock(params)

        params['num_channels'] = 16
        self.decode1 = sm.SDnetDecoderBlock(params)

        self.decode2 = sm.SDnetDecoderBlock(params)

        self.decode3 = sm.SDnetDecoderBlock(params)

        self.decode4 = sm.SDnetDecoderBlock(params)

        params['num_channels'] = 16
        self.classifier = sm.ClassifierBlock(params)
        self.sigmoid = nn.Sigmoid()

        self.fc_layer = nn.Linear(params['num_filters'], 64, bias=True)

    def forward(self, input):
        e1, out1, ind1 = self.encode1(input)

        e2, out2, ind2 = self.encode2(e1)

        e3, _, ind3 = self.encode3(e2)

        e4, _, ind4 = self.encode4(e3)

        bn = self.bottleneck(e4)

        d4 = self.decode4(bn, None, ind4)

        d3 = self.decode3(d4, None, ind3)

        d2 = self.decode2(d3, None, ind2)

        d1 = self.decode1(d2, None, ind1)

        batch_size, num_channels, _, _ = d1.size()
        conv_w = d1.view(batch_size, num_channels, -1).mean(dim=2)
        conv_w = conv_w.mean(dim=0)
        conv_w = self.fc_layer(conv_w)
        return conv_w


class SDnetSegmentor(nn.Module):
    """
    Segmentor Code

    param ={
        'num_channels':1,
        'num_filters':64,
        'kernel_h':5,
        'kernel_w':5,
        'stride_conv':1,
        'pool':2,
        'stride_pool':2,
        'num_classes':1
        'se_block': True,
        'drop_out':0
    }

    """

    def __init__(self, params):
        super(SDnetSegmentor, self).__init__()
        params['num_channels'] = 1
        params['num_filters'] = 64
        self.encode1 = sm.SDnetEncoderBlock(params)
        params['num_channels'] = 64
        self.encode2 = sm.SDnetEncoderBlock(params)
        self.encode3 = sm.SDnetEncoderBlock(params)
        self.encode4 = sm.SDnetEncoderBlock(params)
        self.bottleneck = sm.GenericBlock(params)

        self.decode1 = sm.SDnetDecoderBlock(params)
        self.decode2 = sm.SDnetDecoderBlock(params)
        self.decode3 = sm.SDnetDecoderBlock(params)
        params['num_channels'] = 128
        self.decode4 = sm.SDnetDecoderBlock(params)
        params['num_channels'] = 64
        self.classifier = sm.ClassifierBlock(params)
        self.soft_max = nn.Softmax2d()
        self.sigmoid = nn.Sigmoid()

    def forward(self, inpt, weights=None):
        e1, _, ind1 = self.encode1(inpt)
        e2, _, ind2 = self.encode2(e1)
        e3, _, ind3 = self.encode3(e2)

        e4, out4, ind4 = self.encode4(e3)

        bn = self.bottleneck(e4)

        d4 = self.decode4(bn, out4, ind4)

        d3 = self.decode3(d4, None, ind3)

        d2 = self.decode2(d3, None, ind2)

        d1 = self.decode1(d2, None, ind1)

        if weights is not None:
            channels = weights.size()[0]
            weights = weights.view(1, channels, 1, 1)
            logit = F.conv2d(d1, weights)
        prob = self.sigmoid(logit)
        return prob


class FewShotSegmentorDoubleSDnet(nn.Module):
    '''
    Class Combining Conditioner and Segmentor for few shot learning
    '''

    def __init__(self, params):
        super(FewShotSegmentorDoubleSDnet, self).__init__()
        self.conditioner = SDnetConditioner(params)
        self.segmentor = SDnetSegmentor(params)

    def forward(self, input1, input2):
        weights = self.conditioner(input1)
        segment = self.segmentor(input2, weights)
        return segment

    def enable_test_dropout(self):
        attr_dict = self.__dict__['_modules']
        for i in range(1, 5):
            encode_block, decode_block = attr_dict['encode' + str(i)], attr_dict['decode' + str(i)]
            encode_block.drop_out = encode_block.drop_out.apply(nn.Module.train)
            decode_block.drop_out = decode_block.drop_out.apply(nn.Module.train)

    @property
    def is_cuda(self):
        """
        Check if model parameters are allocated on the GPU.
        """
        return next(self.parameters()).is_cuda

    def save(self, path):
        """
        Save model with its parameters to the given path. Conventionally the
        path should end with "*.model".

        Inputs:
        - path: path string
        """
        print('Saving model... %s' % path)
        torch.save(self, path)

    def predict(self, X, y, query_label, device=0, enable_dropout=False):
        """
        Predicts the outout after the model is trained.
        Inputs:
        - X: Volume to be predicted
        """
        self.eval()
        input1, input2, y2 = split_batch(X, y, query_label)
        input1, input2, y2 = to_cuda(input1, device), to_cuda(input2, device), to_cuda(y2, device)

        if enable_dropout:
            self.enable_test_dropout()

        with torch.no_grad():
            out = self.forward(input1, input2)

        # max_val, idx = torch.max(out, 1)
        idx = out > 0.5
        idx = idx.data.cpu().numpy()
        prediction = np.squeeze(idx)
        del X, out, idx
        return prediction


def to_cuda(X, device):
    if type(X) is np.ndarray:
        X = torch.tensor(X, requires_grad=False).type(torch.FloatTensor).cuda(device, non_blocking=True)
    elif type(X) is torch.Tensor and not X.is_cuda:
        X = X.type(torch.FloatTensor).cuda(device, non_blocking=True)
    return X

import time
import torch
import numpy as np
import json
import multiprocessing
import subprocess
from typing import List, Union
import matplotlib.pyplot as plt
from torch.nn import Conv2d, ConvTranspose2d
from AberrationNN.train_utils import lr_schedule
from AberrationNN.train_utils import Parameters


def collate_fn(batch):
    return tuple(zip(*batch))


def weights_init(module):
    imodules = (Conv2d, ConvTranspose2d)
    if isinstance(module, imodules):
        torch.nn.init.xavier_uniform_(module.weight.data)
        torch.nn.init.zeros_(module.bias)


def set_train_rng(seed: int = 1):
    """
    For reproducibility
    """
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def plot_losses(train_loss: Union[List[float], np.ndarray],
                test_loss: Union[List[float], np.ndarray]) -> None:
    """
    Plots train and test losses
    """
    print('Plotting training history')
    _, ax = plt.subplots(1, 1, figsize=(6, 6))
    ax.plot(train_loss, label='Train')
    ax.plot(test_loss, label='Test')
    ax.set_xlabel('Epoch')
    ax.set_ylabel('Loss')
    ax.legend()
    plt.show()


def get_gpu_info(cuda_device: int) -> int:
    """
    Get the current GPU memory usage
    Adapted with changes from
    https://discuss.pytorch.org/t/access-gpu-memory-usage-in-pytorch/3192/4
    """
    result = subprocess.check_output(
        [
            'nvidia-smi', '--id=' + str(cuda_device),
            '--query-gpu=memory.used,memory.total,utilization.gpu',
            '--format=csv,nounits,noheader'
        ], encoding='utf-8')
    gpu_usage = [int(y) for y in result.split(',')]
    return gpu_usage[0:2]


def train_and_test(model, optimizer, data_loader_train, data_loader_test, device, param, saveckp):
    """
    here epoch is simply defined as single traing step or iters, not looping through whole trainning dataset. 
    """

    l = lr_schedule(param)
    l.plot()
    lr_array = l.schedule

    model.apply(weights_init)

    trainloss_total = []
    testloss_total = []
    record = time.time()

    for i, ((images_train, targets_train), (images_test, targets_test)) in enumerate(
            zip(data_loader_train, data_loader_test)):

        ###Train###
        optimizer.zero_grad()  # stop accumulation of old gradients
        optimizer.param_groups[0]['lr'] = lr_array[i]

        images = images_train.to(device)
        targets = targets_train.to(device)
        # print(next(model.parameters()).is_cuda)
        # print(images.is_cuda, targets .is_cuda)

        pred = model(images)
        lossfunc = torch.nn.SmoothL1Loss()
        trainloss = lossfunc(pred, targets[:, None, :])  # Add a dimension behind the batch dimension for target
        trainloss.backward()  ######!!!!
        optimizer.step()
        trainloss_total.append(trainloss.item())
        ###Test###
        images = images_test.to(device)
        targets = targets_test.to(device)
        model.eval()
        with torch.no_grad():
            pred = model(images)
            testloss = lossfunc(pred, targets[:, None, :])
        testloss_total.append(testloss.item())

        del images, targets  # mannually release GPU memory during training loop.

        if (saveckp is not None) and hasattr(param, 'ckpt_path') and param.ckpt_path is not None:
            check = i % saveckp
            if check == 0 and i > (saveckp - 2):
                checkpoint = {"model": model.state_dict(), "epochs": i,
                              "losses": {'train_loss': trainloss, 'test_loss': testloss}}
                ckpt_path = param.ckpt_path + "/Epoch" + "{}".format(i) + ".pt"
                torch.save(checkpoint, ckpt_path)

        if i % param.print_freq == 0:
            print("Epoch{}\t".format(i), "Train Loss {:.3f}".format(trainloss.item()))
            print("Epoch{}\t".format(i), "Test Loss {:.3f}".format(trainloss.item()),
                  'Cost: {}\t s.'.format(time.time() - record))
            gpu_usage = get_gpu_info(torch.cuda.current_device())
            print('GPU memory usage: {}/{}'.format(gpu_usage[0], gpu_usage[1]))
            record = time.time()

        if i == (param.epochs - 1):
            break

    return trainloss_total, testloss_total, model


def go_train(hyperdict, dataset, device, save_ckp, save_final, **kwargs):
    set_train_rng(1)
    torch.cuda.empty_cache()
    pms = Parameters(**hyperdict)

    indices = torch.randperm(len(dataset)).tolist()
    dataset_train = torch.utils.data.Subset(dataset, indices[:-int(0.4 * len(dataset))])  # swing back to 0.3
    dataset_test = torch.utils.data.Subset(dataset, indices[-int(0.4 * len(dataset)):])

    print('number of train data :', len(dataset_train))
    print('number of test data :', len(dataset_test))
    pool = multiprocessing.Pool()
    # define training and validation data loaders
    d_train = torch.utils.data.DataLoader(
        dataset_train, batch_size=pms.batchsize, shuffle=True, pin_memory=True, num_workers=pool._processes - 8)

    d_test = torch.utils.data.DataLoader(
        dataset_test, batch_size=pms.batchsize, shuffle=False, pin_memory=True, num_workers=pool._processes - 8)

    model = NestedUNet(depth=pms.depth, n_blocks=pms.n_blocks, first_inputchannels=pms.first_inputchannels,
                       activation=pms.activation, dropout=pms.dropput)

    if device == torch.device('cuda'):
        model.cuda()

    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.Adam(params)

    since = time.time()
    trainloss, testloss, trained_model = train_and_test(model, optimizer, d_train, d_test, device, pms, save_ckp)

    plot_losses(trainloss, testloss)

    print("\ntotal time of this training: {:.1f} s".format(time.time() - since))
    if save_final and pms.result_path is not None:
        torch.save({'state_dict': model.state_dict(), 'use_se': True}, pms.result_path + '/statedict.tar')
        with open(pms.result_path + '/hyp.json', 'w+') as fp:
            json.dump(hyperdict, fp)

    return trained_model


class Trainer:
    """
  Call the class will start training. 
  Args:
     hyperdict
     dataset
     device
     save_ckp
     save_final
  """

    def __init__(self, hyperdict, dataset, device, save_ckp, save_final, **kwargs):
        self.trained_model = go_train(hyperdict, dataset, device, save_ckp, save_final)

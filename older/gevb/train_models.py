#import models
import sys
sys.path.append("..")
import vnn
import dfa_util
import vec_models
import nonvec_models
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision
import os

def load_cifar(batch_size=128, shuffle_train=True):
    transform = torchvision.transforms.Compose(
        [torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
    train_set = torchvision.datasets.CIFAR10("../data", train=True, download=True, transform=transform)
    test_set = torchvision.datasets.CIFAR10("../data", train=False, download=True, transform=transform)
    train_loader = torch.utils.data.DataLoader(train_set, batch_size=batch_size, shuffle=shuffle_train)
    test_loader = torch.utils.data.DataLoader(test_set, batch_size=batch_size, shuffle=False)
    return train_loader, test_loader

def load_mnist(batch_size=128, shuffle_train=True):
    transform = torchvision.transforms.Compose(
        [torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.5,), (0.5,))])
    train_set = torchvision.datasets.MNIST("../data", train=True, download=True, transform=transform)
    test_set = torchvision.datasets.MNIST("../data", train=False, download=True, transform=transform)
    train_loader = torch.utils.data.DataLoader(train_set, batch_size=batch_size, shuffle=shuffle_train)
    test_loader = torch.utils.data.DataLoader(test_set, batch_size=batch_size, shuffle=False)
    return train_loader, test_loader

def format_input(data, flatten, vectorized):
    if vectorized:
        if flatten:
            input = vnn.expand_input(torch.flatten(data, 1), 10)
        else:
            input = vnn.expand_input_conv(data, 10)
    else:
        if flatten:
            input = torch.flatten(data, 1)
        else:
            input = data
    return input

def eval_accuracy(model, loader, flatten, vectorized, device):
    loss_sum = 0.
    num_correct = 0
    num_examples = 0
    loss_fn = nn.CrossEntropyLoss(reduction="sum") #note: sum, not mean here
    for batch_idx, (data, labels) in enumerate(loader):
        input = format_input(data, flatten, vectorized)
        with torch.no_grad():
            output = model(input.to(device))
        if vectorized:
            output = output[..., 0]
        loss = loss_fn(output, labels.to(device)).item()
        loss_sum += loss
        num_correct += (output.argmax(dim=1).cpu() == labels).int().sum().item()
        num_examples += len(data)
    accuracy = num_correct / num_examples
    loss = loss_sum / num_examples
    return accuracy, loss

def save_snapshot(snapshot_dir, model, opt, epoch, train_loss, train_accuracy, test_loss, test_accuracy,
    flatten, vectorized, learning_rule):
    torch.save({
        'epoch': epoch,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': opt.state_dict(),
        'train_loss': train_loss,
        'train_accuracy': train_accuracy,
        'test_loss': test_loss,
        'test_accuracy': test_accuracy,
        'flatten': flatten,
        'vectorized': vectorized,
        'learning_rule': learning_rule,
        'device': [p.device for p in model.parameters()][0]
        }, "{}/epoch_{}.pt".format(snapshot_dir, epoch))
    print("saved snapshot at epoch {}".format(epoch))
    print("train/test accuracy: {}/{}".format(train_accuracy, test_accuracy))

def make_dir(dir_name):
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)

def files_in_dir(dir_name):
    #filenames = sorted([os.path.join(dir_name, f) for f in os.listdir(dir_name) if os.path.isfile(os.path.join(dir_name, f))])
    filenames = [f for f in os.listdir(dir_name) if os.path.isfile(os.path.join(dir_name, f))]
    epochs = [int(f.split("_")[1].split(".")[0]) for f in filenames]
    sorted_idx = np.argsort(epochs)
    sorted_filenames = [os.path.join(dir_name, filenames[sorted_idx[i]]) for i in range(len(filenames))]
    return sorted_filenames

def restart_from_snapshot(snapshot_dir, model, opt):
    make_dir(snapshot_dir)
    filenames = files_in_dir(snapshot_dir)
    if len(filenames) > 0:
        snapshot = torch.load(filenames[-1])
        done_training = snapshot['train_accuracy'] == 1.
        if not done_training:
            model.load_state_dict(snapshot['model_state_dict'])
            opt.load_state_dict(snapshot['optimizer_state_dict'])
        epoch = snapshot['epoch']
        restarted = True
        print("loaded model from snapshot at epoch {}".format(epoch))
    else:
        epoch = 0
        restarted = False
        done_training = False
    return epoch, restarted, done_training

def train_model(snapshot_dir, model, train_loader, test_loader, eval_iter, lr, num_epochs,
    flatten, vectorized, learning_rule, device):
    model = model.to(device)
    opt = optim.Adam(model.parameters(), lr=lr)
    _ = model(torch.randn(128, 3, 32, 32).to(device), learning_rule='df') #TODO: remove me!
    snapshot_epoch, just_restarted, done_training = restart_from_snapshot(snapshot_dir, model, opt)
    if done_training or snapshot_epoch >= num_epochs:
        print("Loaded model already done training")
        return
    for epoch in range(snapshot_epoch, num_epochs):
        if epoch % eval_iter == 0 and not just_restarted:
            train_accuracy, train_loss = eval_accuracy(model, train_loader, flatten, vectorized, device)
            test_accuracy, test_loss = eval_accuracy(model, test_loader, flatten, vectorized, device)
            save_snapshot(snapshot_dir, model, opt, epoch, train_loss, train_accuracy, test_loss, test_accuracy,
                flatten, vectorized, learning_rule)
            if train_accuracy == 1.0:
                print("Perfect train accuracy achieved, ending training at epoch {}".format(epoch))
                break
        just_restarted = False
        train_epoch(model, opt, train_loader, flatten, vectorized, learning_rule, device)

def train_epoch(model, opt, train_loader, flatten, vectorized, learning_rule, device):
    avg_loss_sum = 0. #sum of batch-avg loss vals
    num_correct = 0 #sum of correct counts
    num_examples = 0 #total # examples
    loss_fn = nn.CrossEntropyLoss(reduction="mean")
    for batch_idx, (data, labels) in enumerate(train_loader):
        input = format_input(data, flatten, vectorized)
        opt.zero_grad()
        if vectorized:
            #vectorized BP or DF
            with torch.no_grad(): #makes no difference...but this proves to ourselves that there's no gradient here!
                output = model(input.to(device))[..., 0]
            vnn.set_model_grads(model, output, labels, learning_rule=learning_rule, reduction="mean")
            loss = loss_fn(output, labels.to(device))
        else:
            #unvectorized BP or DF
            output = model(input.to(device), learning_rule=learning_rule)
            loss = loss_fn(output, labels.to(device))
            loss.backward()
        opt.step()
        if vectorized:
            vnn.post_step_callback(model)
        else:
            dfa_util.post_step_callback(model)
        avg_loss_sum += loss.item()
        num_correct += (output.detach().argmax(dim=1).cpu() == labels).int().sum().item()
        num_examples += len(data)
    epoch_loss = avg_loss_sum / (batch_idx + 1)
    epoch_accuracy = num_correct / num_examples
    print("loss: {}, accuracy: {}".format(epoch_loss, epoch_accuracy))
        
def run_mnist_vec_experiments(model_dir, eval_iter, lr, num_epochs, device="cpu", experiment_indices=None):
    train_loader, test_loader = load_mnist()
    common_params = {
        "train_loader": train_loader,
        "test_loader": test_loader,
        "eval_iter": eval_iter,
        "lr": lr,
        "num_epochs": num_epochs,
        "device": device,
        "vectorized": True}
    if experiment_indices is None:
        experiment_indices = np.arange(12)
    for i in experiment_indices:
        #Fully connected
        if i == 0:
            model = vec_models.make_mnist_vec_fc(mono=False)
            train_model(model_dir + "/mnist_vec_fc_bp_mixed", model, **common_params, flatten=True, learning_rule="bp")
        elif i == 1:
            model = vec_models.make_mnist_vec_fc(mono=True)
            train_model(model_dir + "/mnist_vec_fc_bp_mono", model, **common_params, flatten=True, learning_rule="bp")
        elif i == 2:
            model = vec_models.make_mnist_vec_fc(mono=False)
            train_model(model_dir + "/mnist_vec_fc_df_mixed", model, **common_params, flatten=True, learning_rule="df")
        elif i == 3:
            model = vec_models.make_mnist_vec_fc(mono=True)
            train_model(model_dir + "/mnist_vec_fc_df_mono", model, **common_params, flatten=True, learning_rule="df")
        #Convolutional
        elif i == 4:
            model = vec_models.make_mnist_vec_conv(mono=False)
            train_model(model_dir + "/mnist_vec_conv_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 5:
            model = vec_models.make_mnist_vec_conv(mono=True)
            train_model(model_dir + "/mnist_vec_conv_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 6:
            model = vec_models.make_mnist_vec_conv(mono=False)
            train_model(model_dir + "/mnist_vec_conv_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 7:
            model = vec_models.make_mnist_vec_conv(mono=True)
            train_model(model_dir + "/mnist_vec_conv_df_mono", model, **common_params, flatten=False, learning_rule="df")
        #Locally connected
        elif i == 8:
            model = vec_models.make_mnist_vec_lc(mono=False)
            train_model(model_dir + "/mnist_vec_lc_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 9:
            model = vec_models.make_mnist_vec_lc(mono=True)
            train_model(model_dir + "/mnist_vec_lc_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 10:
            model = vec_models.make_mnist_vec_lc(mono=False)
            train_model(model_dir + "/mnist_vec_lc_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 11:
            model = vec_models.make_mnist_vec_lc(mono=True)
            train_model(model_dir + "/mnist_vec_lc_df_mono", model, **common_params, flatten=False, learning_rule="df")

def run_cifar_vec_experiments(model_dir, eval_iter, lr, num_epochs, device="cpu", experiment_indices=None):
    train_loader, test_loader = load_cifar()
    common_params = {
        "train_loader": train_loader,
        "test_loader": test_loader,
        "eval_iter": eval_iter,
        "lr": lr,
        "num_epochs": num_epochs,
        "device": device,
        "vectorized": True}
    if experiment_indices is None:
        experiment_indices = np.arange(12)
    for i in experiment_indices:
        #Fully connected
        if i == 0:
            model = vec_models.make_cifar_vec_fc(mono=False)
            train_model(model_dir + "/cifar_vec_fc_bp_mixed", model, **common_params, flatten=True, learning_rule="bp")
        elif i == 1:
            model = vec_models.make_cifar_vec_fc(mono=True)
            train_model(model_dir + "/cifar_vec_fc_bp_mono", model, **common_params,flatten=True, learning_rule="bp")
        elif i == 2:
            model = vec_models.make_cifar_vec_fc(mono=False)
            train_model(model_dir + "/cifar_vec_fc_df_mixed", model, **common_params,flatten=True, learning_rule="df")
        elif i == 3:
            model = vec_models.make_cifar_vec_fc(mono=True)
            train_model(model_dir + "/cifar_vec_fc_df_mono", model, **common_params,flatten=True, learning_rule="df")
        #Convolutional
        elif i == 4:
            model = vec_models.make_cifar_vec_conv(mono=False)
            train_model(model_dir + "/cifar_vec_conv_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 5:
            model = vec_models.make_cifar_vec_conv(mono=True)
            train_model(model_dir + "/cifar_vec_conv_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 6:
            model = vec_models.make_cifar_vec_conv(mono=False)
            train_model(model_dir + "/cifar_vec_conv_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 7:
            model = vec_models.make_cifar_vec_conv(mono=True)
            train_model(model_dir + "/cifar_vec_conv_df_mono", model, **common_params, flatten=False, learning_rule="df")
        #Locally connected
        elif i == 8:
            model = vec_models.make_cifar_vec_lc(mono=False)
            train_model(model_dir + "/cifar_vec_lc_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 9:
            model = vec_models.make_cifar_vec_lc(mono=True)
            train_model(model_dir + "/cifar_vec_lc_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 10:
            model = vec_models.make_cifar_vec_lc(mono=False)
            train_model(model_dir + "/cifar_vec_lc_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 11:
            model = vec_models.make_cifar_vec_lc(mono=True)
            train_model(model_dir + "/cifar_vec_lc_df_mono", model, **common_params, flatten=False, learning_rule="df")

def run_mnist_nonvec_experiments(model_dir, eval_iter, lr, num_epochs, device="cpu", experiment_indices=None):
    train_loader, test_loader = load_mnist()
    common_params = {
        "train_loader": train_loader,
        "test_loader": test_loader,
        "eval_iter": eval_iter,
        "lr": lr,
        "num_epochs": num_epochs,
        "device": device,
        "vectorized": False}
    if experiment_indices is None:
        experiment_indices = np.arange(12)
    for i in experiment_indices:
        #Fully connected
        if i == 0:
            model = nonvec_models.make_mnist_nonvec_fc(mono=False)
            train_model(model_dir + "/mnist_nonvec_fc_bp_mixed", model, **common_params, flatten=True, learning_rule="bp")
        elif i == 1:
            model = nonvec_models.make_mnist_nonvec_fc(mono=True)
            train_model(model_dir + "/mnist_nonvec_fc_bp_mono", model, **common_params, flatten=True, learning_rule="bp")
        elif i == 2:
            model = nonvec_models.make_mnist_nonvec_fc(mono=False)
            train_model(model_dir + "/mnist_nonvec_fc_df_mixed", model, **common_params, flatten=True, learning_rule="df")
        elif i == 3:
            model = nonvec_models.make_mnist_nonvec_fc(mono=True)
            train_model(model_dir + "/mnist_nonvec_fc_df_mono", model, **common_params, flatten=True, learning_rule="df")
        #Convolutional
        elif i == 4:
            model = nonvec_models.make_mnist_nonvec_conv(mono=False)
            train_model(model_dir + "/mnist_nonvec_conv_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 5:
            model = nonvec_models.make_mnist_nonvec_conv(mono=True)
            train_model(model_dir + "/mnist_nonvec_conv_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 6:
            model = nonvec_models.make_mnist_nonvec_conv(mono=False)
            train_model(model_dir + "/mnist_nonvec_conv_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 7:
            model = nonvec_models.make_mnist_nonvec_conv(mono=True)
            train_model(model_dir + "/mnist_nonvec_conv_df_mono", model, **common_params, flatten=False, learning_rule="df")
        #Locally connected
        elif i == 8:
            model = nonvec_models.make_mnist_nonvec_lc(mono=False)
            train_model(model_dir + "/mnist_nonvec_lc_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 9:
            model = nonvec_models.make_mnist_nonvec_lc(mono=True)
            train_model(model_dir + "/mnist_nonvec_lc_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 10:
            model = nonvec_models.make_mnist_nonvec_lc(mono=False)
            train_model(model_dir + "/mnist_nonvec_lc_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 11:
            model = nonvec_models.make_mnist_nonvec_lc(mono=True)
            train_model(model_dir + "/mnist_nonvec_lc_df_mono", model, **common_params, flatten=False, learning_rule="df")

def run_cifar_nonvec_experiments(model_dir, eval_iter, lr, num_epochs, device="cpu", experiment_indices=None):
    train_loader, test_loader = load_cifar()
    common_params = {
        "train_loader": train_loader,
        "test_loader": test_loader,
        "eval_iter": eval_iter,
        "lr": lr,
        "num_epochs": num_epochs,
        "device": device,
        "vectorized": False}
    if experiment_indices is None:
        experiment_indices = np.arange(12)
    for i in experiment_indices:
        #Fully connected
        if i == 0:
            model = nonvec_models.make_cifar_nonvec_fc(mono=False)
            train_model(model_dir + "/cifar_nonvec_fc_bp_mixed", model, **common_params, flatten=True, learning_rule="bp")
        elif i == 1:
            model = nonvec_models.make_cifar_nonvec_fc(mono=True)
            train_model(model_dir + "/cifar_nonvec_fc_bp_mono", model, **common_params,flatten=True, learning_rule="bp")
        elif i == 2:
            model = nonvec_models.make_cifar_nonvec_fc(mono=False)
            train_model(model_dir + "/cifar_nonvec_fc_df_mixed", model, **common_params,flatten=True, learning_rule="df")
        elif i == 3:
            model = nonvec_models.make_cifar_nonvec_fc(mono=True)
            train_model(model_dir + "/cifar_nonvec_fc_df_mono", model, **common_params,flatten=True, learning_rule="df")
        #Convolutional
        elif i == 4:
            model = nonvec_models.make_cifar_nonvec_conv(mono=False)
            train_model(model_dir + "/cifar_nonvec_conv_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 5:
            model = nonvec_models.make_cifar_nonvec_conv(mono=True)
            train_model(model_dir + "/cifar_nonvec_conv_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 6:
            model = nonvec_models.make_cifar_nonvec_conv(mono=False)
            train_model(model_dir + "/cifar_nonvec_conv_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 7:
            model = nonvec_models.make_cifar_nonvec_conv(mono=True)
            train_model(model_dir + "/cifar_nonvec_conv_df_mono", model, **common_params, flatten=False, learning_rule="df")
        #Locally connected
        elif i == 8:
            model = nonvec_models.make_cifar_nonvec_lc(mono=False)
            train_model(model_dir + "/cifar_nonvec_lc_bp_mixed", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 9:
            model = nonvec_models.make_cifar_nonvec_lc(mono=True)
            train_model(model_dir + "/cifar_nonvec_lc_bp_mono", model, **common_params, flatten=False, learning_rule="bp")
        elif i == 10:
            model = nonvec_models.make_cifar_nonvec_lc(mono=False)
            train_model(model_dir + "/cifar_nonvec_lc_df_mixed", model, **common_params, flatten=False, learning_rule="df")
        elif i == 11:
            model = nonvec_models.make_cifar_nonvec_lc(mono=True)
            train_model(model_dir + "/cifar_nonvec_lc_df_mono", model, **common_params, flatten=False, learning_rule="df")

if __name__ == "__main__":
    #for i in range(1, 5):
    #i = 1
    #model_dir = 'experiments/models_' + str(i)
    # print('Here')
    # model_dir = '/media/davidclark/DATA/VectorizedNets/models_2'
    model_dir = 'C:/Users/abalwani6/Documents/Aish/Summer 2022/Flatiron-CCN/Recurrent-GEVB/models_gevb'
    #run_mnist_vec_experiments(model_dir, eval_iter=10, lr=3e-4, num_epochs=200, device=0)
    #run_cifar_vec_experiments(model_dir, eval_iter=10, lr=3e-4, num_epochs=200, device=0)
    #run_mnist_nonvec_experiments(model_dir, eval_iter=10, lr=3e-4, num_epochs=200, device=0)
    run_cifar_nonvec_experiments(model_dir, eval_iter=10, lr=3e-4, num_epochs=200, device=0, experiment_indices=np.array([11]))











































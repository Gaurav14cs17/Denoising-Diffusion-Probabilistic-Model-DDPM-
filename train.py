from ddpm import *
from  dataloader import *
from  model_2 import *
from dataclasses import dataclass
import torch.nn.functional as F


@dataclass
class TrainingConfig:
    image_size = 128
    image_channels = 3
    train_batch_size = 6
    eval_batch_size = 6
    num_epochs = 150
    start_epoch = 0
    learning_rate = 2e-5
    diffusion_timesteps = 1000
    save_image_epochs = 5
    save_model_epochs = 5
    dataset = 'huggan/smithsonian_butterflies_subset'
    output_dir = f'models/{dataset.split("/")[-1]}'
    device = "cuda"
    seed = 0
    resume = None


training_config = TrainingConfig()


def evaluate(config, epoch, pipeline, model):
    noisy_sample = torch.randn(config.eval_batch_size, config.image_channels, config.image_size, config.image_size).to(
        config.device)
    images = pipeline.sampling(model, noisy_sample, device=config.device)
    images = postprocess(images)
    image_grid = create_images_grid(images, rows=2, cols=3)
    grid_save_dir = Path(config.output_dir, "samples")
    grid_save_dir.mkdir(parents=True, exist_ok=True)
    image_grid.save(f"{grid_save_dir}/{epoch:04d}.png")


def main():
    train_dataloader = get_loader(training_config)
    model = Unet(dim=training_config.image_size, channels=3, dim_mults=(1, 2, 4,)).to(training_config.device)

    print("Model size: ", sum([p.numel() for p in model.parameters() if p.requires_grad]))
    optimizer = torch.optim.Adam(model.parameters(), lr=training_config.learning_rate)
    lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer=optimizer,
                                                              T_max=len(train_dataloader) * training_config.num_epochs,
                                                              last_epoch=-1,
                                                              eta_min=1e-9)

    if training_config.resume:
        checkpoint = torch.load(training_config.resume, map_location='cpu')
        model.load_state_dict(checkpoint['model'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        lr_scheduler.load_state_dict(checkpoint['lr_scheduler'])
        training_config.start_epoch = checkpoint['epoch'] + 1

    for param_group in optimizer.param_groups:
        param_group['lr'] = training_config.learning_rate

    diffusion_pipeline = DDPMPipeline(beta_start=1e-4, beta_end=1e-2, num_timesteps=training_config.diffusion_timesteps,
                                      device=training_config.device)
    global_step = training_config.start_epoch * len(train_dataloader)
    for epoch in range(training_config.start_epoch, training_config.num_epochs):
        progress_bar = tqdm(total=len(train_dataloader))
        progress_bar.set_description(f"Epoch {epoch}")
        mean_loss = 0
        model.train()
        for step, batch in enumerate(train_dataloader):
            original_images = batch['images'].to(training_config.device)
            batch_size = original_images.shape[0]
            timesteps = torch.randint(0, diffusion_pipeline.num_timesteps, (batch_size,),
                                      device=training_config.device).long()
            noisy_images, noise = diffusion_pipeline.forward_diffusion(original_images, timesteps)
            noisy_images = noisy_images.to(training_config.device)
            noise_pred = model(noisy_images, timesteps)
            loss = F.mse_loss(noise_pred, noise)
            mean_loss = mean_loss + (loss.detach().item() - mean_loss) / (step + 1)
            loss.backward()

            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            lr_scheduler.step()
            optimizer.zero_grad()

            progress_bar.update(1)
            logs = {"loss": mean_loss, "lr": lr_scheduler.get_last_lr()[0], "step": global_step}
            progress_bar.set_postfix(**logs)
            global_step += 1

        if (epoch + 1) % training_config.save_image_epochs == 0 or epoch == training_config.num_epochs - 1:
            model.eval()
            evaluate(training_config, epoch, diffusion_pipeline, model)

        if (epoch + 1) % training_config.save_model_epochs == 0 or epoch == training_config.num_epochs - 1:
            checkpoint = {
                'model': model.state_dict(),
                'optimizer': optimizer.state_dict(),
                'lr_scheduler': lr_scheduler.state_dict(),
                'parameters': training_config,
                'epoch': epoch
            }
            torch.save(checkpoint, Path(training_config.output_dir,
                                        f"unet{training_config.image_size}_e{epoch}.pth"))


if __name__ == "__main__":
    main()

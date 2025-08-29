import os

import boto3
import requests
from PIL import Image
from io import BytesIO

"""
Use the AWS Command-Line Interface (CLI) aws configure command to store your credentials in a file, 
which will be automatically used by your code.

To make all objects in a bucket publicly readable, create a Bucket Policy on that specific bucket, 
which grants GetObject permissions for anonymous users. Replace examplebucket with the name of the bucket. 
You can add the Bucket Policy in the Amazon S3 Management Console, in the Permissions section.

{
  "Statement":[
    {
      "Sid": "AddPerm",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::examplebucket/*"
    }
  ]
}
"""


class S3ImagesInvalidExtension(Exception):
    pass


class S3ImagesUploadFailed(Exception):
    pass


class S3:
    def __init__(self, bucket_name, region_name='eu-west-1'):
        self.client = boto3.client('s3', region_name=region_name)
        self.bucket_name = bucket_name

    def add(self, file_path, object_name=None):
        if not object_name:
            object_name = file_path
        self.client.upload_file(file_path, self.bucket_name, object_name)
        return self.url(object_name)

    def add_from_url(self, url, object_name):
        """ Add an image from a url to the bucket, with name object_name"""
        response = requests.get(url)
        if response.status_code == 200:
            self.client.put_object(Bucket=self.bucket_name, Key=object_name, Body=response.content)
            return self.url(object_name)
        else:
            raise S3ImagesUploadFailed(f'Failed to get image from {url}')

    def add_from_file_data(self, data, object_name, mime_type=None):
        """ Add an image from a url to the bucket, with name object_name"""
        print('S3 ADD FROM FILE DATA', object_name, mime_type)
        self.client.put_object(Bucket=self.bucket_name, Key=object_name, Body=data, ContentType=mime_type)
        return self.url(object_name)

    def get_data(self, object_name):
        """ Returns the content of the object as bytes"""
        return self.client.get_object(Bucket=self.bucket_name, Key=object_name)['Body'].read()

    def add_from_pil_image(self, image: Image, object_name: str):
        def get_safe_ext(key):
            ext = os.path.splitext(key)[-1].strip('.').upper()
            if ext in ['JPG', 'JPEG']:
                return 'JPEG'
            elif ext in ['PNG']:
                return 'PNG'
            elif ext in ['WEBP']:
                return 'WEBP'
            raise S3ImagesInvalidExtension('Extension is invalid')

        buffer = BytesIO()
        image.image.save(buffer, get_safe_ext(object_name))
        buffer.seek(0)
        sent_data = self.client.put_object(Bucket=self.bucket_name, Key=object_name, Body=buffer)
        if sent_data['ResponseMetadata']['HTTPStatusCode'] != 200:
            raise S3ImagesUploadFailed('Failed to upload image {} to bucket {}'.format(object_name, self.bucket_name))
        return self.url(object_name)

    def download(self, object_name, file_path):
        self.client.download_file(self.bucket_name, object_name, file_path)

    def delete(self, object_name):
        response = self.client.delete_object(Bucket=self.bucket_name, Key=object_name)
        return response

    def list(self):
        response = self.client.list_objects_v2(Bucket=self.bucket_name)
        return [c['Key'] for c in response['Contents']]

    def head_object(self, object_name):
        response = self.client.head_object(Bucket=self.bucket_name, Key=object_name)
        return response

    def mime_type(self, object_name):
        return self.head_object(object_name)['ContentType']

    def url(self, object_name):
        """ return the public url of an object in the bucket"""
        return f'https://s3.{self.client.meta.region_name}.amazonaws.com/{self.bucket_name}/{object_name}'

    def sized(self, object_name: str, size: tuple):
        """ Returns the url of the image given by object name, resized to size
        If the sized version does not exist in the bucket, use PIL to create it."""
        w, h = size
        name, ext = object_name.rsplit('.', 1)
        sized_name = f'{name}_{w}x{h}.{ext}'
        if sized_name not in self.list():
            self.create_sized(object_name, sized_name, size)
        return self.url(sized_name)

    def create_sized(self, object_name: str, sized_name: str, size: tuple):
        """ Creates a sized version of object_name in the bucket, with name sized_name"""

        data = self.get_data(object_name)
        image = Image.open(BytesIO(data))
        image.thumbnail(size)
        return self.add_from_pil_image(image, sized_name)


if __name__ == '__main__':
    s3 = S3('harmsen.nl')
    s3.add('/users/hp/Downloads/retriever.jpg', 'retriever.jpg')
    # s3.delete('linkedin.png')
    # response = s3.list()
    # print(response)
    # print(s3.url('icons/linkedin.png'))
    print(s3.sized('retriever.jpg', (30, 30)))

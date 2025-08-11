# Use an official Node.js runtime as base
FROM node:18-alpine

# Set working directory
WORKDIR /usr/src/app

# Copy package files (if you have package.json)
COPY package*.json ./ 

# Install dependencies
RUN npm install ci || true

# Copy all files
COPY . .

# Expose port 8080 (GKE will use this)
EXPOSE 8080

# Start the Node.js app
CMD ["node", "src/server.js"]

module.exports = {
	  testEnvironment: 'node',
	  testMatch: ['**/__tests__/**/*.test.js', '**/tests/**/*.test.js'],
	  reporters: ['default', ['jest-junit', { outputDirectory: '.', outputName: 'junit.xml' }]]
};

